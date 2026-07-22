@echo off
echo ============================================
echo  WIPO Webapp - Push to GitHub
echo ============================================
echo.
cd /d "C:\Users\xugen\WorkBuddy\2026-07-06-16-41-01\wipo-webapp"
echo Pushing to https://github.com/xugengli/wipo-webapp.git ...
echo.
echo If a browser window pops up, please login to GitHub to authorize.
echo.
git push -u origin main
echo.
echo ============================================
if %ERRORLEVEL% EQU 0 (
    echo SUCCESS! Code pushed to GitHub.
) else (
    echo FAILED. Error code: %ERRORLEVEL%
)
echo ============================================
pause
