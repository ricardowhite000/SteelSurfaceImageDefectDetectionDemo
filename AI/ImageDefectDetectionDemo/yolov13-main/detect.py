
from ultralytics import YOLO

if __name__ =='__main__':
    #load a model
    model = YOLO(model=r'G:\yolov13-main\yolov13n.pt')
    model.predict(source=r'G:\yolov13-main\11.mp4',
                  save=True,
                  show=True,
                  )
