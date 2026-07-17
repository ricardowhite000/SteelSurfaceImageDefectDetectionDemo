from __future__ import annotations

import argparse
import csv
import gc
import json
import os
from pathlib import Path


def _atomic_text(path: Path, text: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _write_review_csv(path: Path, rows: list[dict]) -> None:
    temporary=path.with_suffix(".tmp");fieldnames=["filename","expected_class_id","predicted_class_ids","box_count","min_confidence","max_confidence","status"]
    with temporary.open("w",newline="",encoding="utf-8-sig") as stream:
        writer=csv.DictWriter(stream,fieldnames=fieldnames);writer.writeheader();writer.writerows(rows)
    os.replace(temporary,path)


def main() -> int:
    parser=argparse.ArgumentParser(description="平台流式推理执行器：batch=1、断点续跑、原子写入。")
    parser.add_argument("--source-list",type=Path,required=True);parser.add_argument("--weights",type=Path,required=True);parser.add_argument("--output",type=Path,required=True);parser.add_argument("--batch",type=int,default=1);parser.add_argument("--device",default="0");parser.add_argument("--conf",type=float,default=.20);parser.add_argument("--review-conf",type=float,default=.40);parser.add_argument("--imgsz",type=int,default=640);parser.add_argument("--classes",nargs="+",required=True)
    args=parser.parse_args()
    if args.batch!=1:parser.error("平台Demo固定batch=1，防止全量推理显存持续增长")
    if not args.source_list.is_file() or not args.weights.is_file():parser.error("来源清单或模型权重不存在")
    args.output.mkdir(parents=True,exist_ok=True);checkpoint=args.output/"processed.jsonl";processed=set()
    checkpoint_rows=[]
    if checkpoint.is_file():
        for line in checkpoint.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record=json.loads(line);processed.add(record["filename"])
                if isinstance(record.get("review"),dict):checkpoint_rows.append(record["review"])
    sources=[Path(line.strip()) for line in args.source_list.read_text(encoding="utf-8").splitlines() if line.strip()];remaining=[path for path in sources if path.name not in processed]
    from ultralytics import YOLO
    from steel_tutorial.model_tools import pseudo_label_rows
    model=YOLO(str(args.weights));rows=[];review_path=args.output/"pseudo_review.csv"
    if review_path.is_file():rows=list(csv.DictReader(review_path.open(newline="",encoding="utf-8-sig")))
    elif checkpoint_rows:rows=checkpoint_rows;_write_review_csv(review_path,rows)
    if not remaining:
        if not review_path.is_file():_write_review_csv(review_path,rows)
        print(f"流式推理已完成：{len(processed)}/{len(sources)}");return 0
    for index,path in enumerate(remaining,1):
        predictions=model.predict(source=str(path),batch=1,conf=args.conf,imgsz=args.imgsz,device=args.device,stream=False,save=False,verbose=False)
        if len(predictions)!=1:raise RuntimeError(f"单图推理返回了异常结果数：{len(predictions)}")
        result=predictions[0]
        labels,review=pseudo_label_rows(result,args.review_conf);target=args.output/f"{Path(result.path).stem}.txt"
        if labels:_atomic_text(target,"\n".join(labels)+"\n")
        rows.append(review)
        with checkpoint.open("a",encoding="utf-8") as stream:stream.write(json.dumps({"filename":Path(result.path).name,"review":review},ensure_ascii=False)+"\n");stream.flush();os.fsync(stream.fileno())
        del result,predictions
        if index%25==0 or index==len(remaining):
            _write_review_csv(review_path,rows);print(f"流式推理：{len(processed)+index}/{len(sources)}");gc.collect()
            try:
                import torch
                if torch.cuda.is_available():torch.cuda.empty_cache()
            except ImportError:pass
    return 0


if __name__=="__main__":raise SystemExit(main())
