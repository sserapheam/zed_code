@echo off
echo Настройка сетевого доступа для Flask приложения...
echo.

echo 1. Открытие порта 5000 в брандмауэре Windows...
netsh advfirewall firewall add rule name="Flask App Port 5000" dir=in action=allow protocol=TCP localport=5000
if %errorlevel% equ 0 (
    echo ✓ Порт 5000 открыт в брандмауэре
) else (
    echo ✗ Ошибка при открытии порта. Запустите скрипт от имени администратора!
)

echo.
echo 2. Получение IP адреса...
for /f "tokens=2 delims=:" %%i in ('ipconfig ^| findstr /i "IPv4"') do (
    set ip=%%i
    goto :found
)
:found
echo Ваш локальный IP адрес: %ip%
echo.

echo 3. Инструкции для доступа:
echo.
echo Для доступа из локальной сети:
echo http://%ip%:5000
echo.
echo Для доступа из интернета:
echo 1. Узнайте ваш внешний IP: https://whatismyipaddress.com/
echo 2. Настройте проброс портов в роутере (Port Forwarding)
echo 3. Используйте: http://ВАШ_ВНЕШНИЙ_IP:5000
echo.
echo ВАЖНО: 
echo - Отключите debug=True для продакшена
echo - Используйте HTTPS для безопасности
echo - Настройте аутентификацию
echo.
pause
