@REM Minimal Maven Wrapper launcher for Windows.
@ECHO OFF
SETLOCAL
SET BASEDIR=%~dp0
IF EXIST "%BASEDIR%.mvn\wrapper\maven-wrapper.jar" GOTO runwrapper
WHERE mvn >NUL 2>NUL
IF %ERRORLEVEL% EQU 0 (mvn %* & EXIT /B %ERRORLEVEL%)
ECHO Maven is not installed and maven-wrapper.jar is not present.
ECHO Run Maven Wrapper setup once or install Maven 3.9+.
EXIT /B 1
:runwrapper
java -Dmaven.multiModuleProjectDirectory="%BASEDIR:~0,-1%" -classpath "%BASEDIR%.mvn\wrapper\maven-wrapper.jar" org.apache.maven.wrapper.MavenWrapperMain %*
