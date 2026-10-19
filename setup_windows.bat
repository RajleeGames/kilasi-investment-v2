@echo off
py -m venv venv
call venv\Scripts\activate
pip install -r requirements.txt
python manage.py makemigrations core
python manage.py migrate
python manage.py seed_demo
echo.
echo Setup complete. Run start_windows.bat
echo Admin: admin / Mega@12345
pause
