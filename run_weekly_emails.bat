@echo off
cd /d "C:\Users\Usuario\Desktop\Faro-index"
python send_emails_weekly.py >> email_weekly.log 2>&1
