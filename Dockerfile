FROM python:3.9-slim

# Устанавливаем FFmpeg и зависимости
RUN apt-get update && apt-get install -y ffmpeg && apt-get clean

# Проверяем, что FFmpeg установлен
RUN ffmpeg -version

# Копируем проект
WORKDIR /app
COPY . .

# Устанавливаем зависимости Python
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Указываем порт
EXPOSE 5000

# Запускаем приложение с увеличенным тайм-аутом
CMD ["gunicorn", "app:app", "--timeout", "1200", "--bind", "0.0.0.0:5000"]