@echo off
echo Настройка сетевого доступа для Flask приложения (порт 8080)...
echo.

echo 1. Открытие порта 8080 в брандмауэре Windows...
netsh advfirewall firewall add rule name="Flask App Port 8080" dir=in action=allow protocol=TCP localport=8080
if %errorlevel% equ 0 (
    echo Порт 8080 открыт в брандмауэре
) else (
    echo Ошибка при открытии порта. Запустите скрипт от имени администратора!
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
echo http://%ip%:8080
echo.
echo Для доступа из интернета:
echo 1. Узнайте ваш внешний IP: https://whatismyipaddress.com/
echo 2. Настройте проброс портов в роутере (Port Forwarding):
echo    - Внешний порт: 8080
echo    - Внутренний порт: 8080
echo    - Внутренний IP: %ip%
echo    - Протокол: TCP
echo 3. Используйте: http://ВАШ_ВНЕШНИЙ_IP:8080
echo.
echo ВАЖНО: 
echo - Отключите debug=True для продакшена
echo - Используйте HTTPS для безопасности
echo - Настройте аутентификацию
echo.
pause
