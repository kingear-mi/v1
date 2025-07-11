from flask import Flask, request, send_file, render_template, jsonify, Response
from flask_cors import CORS
import yt_dlp
import os
import shutil
import subprocess
import logging
import browser_cookie3
from time import sleep
import uuid
import requests
from bs4 import BeautifulSoup

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Словарь для хранения прогресса
download_progress = {}

def get_ffmpeg_path():
    # Try to find ffmpeg in system PATH
    ffmpeg_path = shutil.which('ffmpeg')
    if ffmpeg_path:
        return ffmpeg_path
    # Fallback to common Homebrew paths
    for path in ['/opt/homebrew/bin/ffmpeg', '/usr/local/bin/ffmpeg']:
        if os.path.exists(path):
            return path
    return None

def get_browser_cookies():
    try:
        cookies = browser_cookie3.chrome(domain_name='youtube.com')
        cookie_dict = {cookie.name: cookie.value for cookie in cookies}
        logger.info("Cookies успешно извлечены из браузера")
        return cookie_dict
    except Exception as e:
        logger.error(f"Ошибка при извлечении cookies: {str(e)}")
        return {}

def progress_hook(d, download_id):
    if d['status'] == 'downloading':
        total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
        downloaded_bytes = d.get('downloaded_bytes', 0)
        if total_bytes > 0:
            progress = int((downloaded_bytes / total_bytes) * 100)
            download_progress[download_id] = progress
    elif d['status'] == 'finished':
        download_progress[download_id] = 100

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/progress/<download_id>')
def progress(download_id):
    def generate():
        while True:
            progress = download_progress.get(download_id, 0)
            yield f"data: {progress}\n\n"
            if progress >= 100:
                break
            sleep(0.1)
        if download_id in download_progress:
            del download_progress[download_id]
    return Response(generate(), mimetype='text/event-stream')

@app.route('/metadata', methods=['POST'])
def metadata():
    data = request.get_json()
    url = data.get('url')
    if not url:
        return jsonify({'error': 'URL не указан'}), 400

    cookies = get_browser_cookies()
    ydl_opts = {
        'noplaylist': True,
        'cookies': cookies,
        'user_agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
        'skip_download': True,
        'quiet': True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            title = info.get('title', 'Без названия')
            description = info.get('description', 'Описание отсутствует')
            thumbnail = info.get('thumbnail', '')

        return jsonify({
            'status': 'success',
            'title': title,
            'description': description,
            'thumbnail': thumbnail
        })
    except Exception as e:
        logger.error(f"Ошибка при извлечении метаданных: {str(e)}")
        return jsonify({'error': f'Ошибка: {str(e)}'}), 500

@app.route('/api/metadata', methods=['POST', 'OPTIONS'])
def api_metadata():
    if request.method == 'OPTIONS':
        response = jsonify({'status': 'ok'})
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, ngrok-skip-browser-warning'
        return response
    return metadata()

@app.route('/download', methods=['POST'])
def download():
    data = request.get_json()
    url = data.get('url')
    format = data.get('format')
    download_id = data.get('download_id')

    if not url or not format or not download_id:
        return jsonify({'error': 'URL, формат или download_id не указаны'}), 400

    download_progress[download_id] = 0

    ffmpeg_path = get_ffmpeg_path()
    if not ffmpeg_path:
        return jsonify({'error': 'FFmpeg не установлен или не найден'}), 500

    try:
        subprocess.run([ffmpeg_path, '-version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return jsonify({'error': 'FFmpeg не работает'}), 500

    download_dir = 'downloads'
    if not os.path.exists(download_dir):
        os.makedirs(download_dir)

    output_template = f'{download_dir}/%(title)s.%(ext)s'
    cookies = get_browser_cookies()

    ydl_opts = {
        'outtmpl': output_template,
        'noplaylist': True,
        'ffmpeg_location': ffmpeg_path,
        'cookies': cookies,
        'user_agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
        'progress_hooks': [lambda d: progress_hook(d, download_id)],
    }

    if format == 'mp4':
        ydl_opts.update({
            'format': 'bestvideo[vcodec^=avc1][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]',
            'merge_output_format': 'mp4',
            'postprocessors': [{
                'key': 'FFmpegVideoRemuxer',
                'preferedformat': 'mp4',
            }],
        })
    elif format == 'mp3':
        ydl_opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        })
    else:
        return jsonify({'error': 'Неподдерживаемый формат'}), 400

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get('title', 'unknown').replace('/', '_').replace('\\', '_')
            file_path = f'{download_dir}/{title}.{format}'
        
        if not os.path.exists(file_path):
            for f in os.listdir(download_dir):
                if f.endswith(f'.{format}'):
                    file_path = f'{download_dir}/{f}'
                    title = f[:-4]
                    break
            else:
                raise FileNotFoundError(f"Файл с расширением .{format} не найден")

        response = send_file(file_path, as_attachment=True, download_name=f'{title}.{format}')
        shutil.rmtree(download_dir, ignore_errors=True)
        return response

    except Exception as e:
        shutil.rmtree(download_dir, ignore_errors=True)
        if download_id in download_progress:
            del download_progress[download_id]
        return jsonify({'error': f'Ошибка: {str(e)}'}), 500

@app.route('/api/download', methods=['POST', 'OPTIONS'])
def api_download():
    if request.method == 'OPTIONS':
        response = jsonify({'status': 'ok'})
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, ngrok-skip-browser-warning'
        return response

    data = request.get_json()
    url = data.get('url')
    format = data.get('format')
    download_id = data.get('download_id')

    if not url or not format or not download_id:
        return jsonify({'error': 'URL, формат или download_id не указаны'}), 400

    download_progress[download_id] = 0

    ffmpeg_path = get_ffmpeg_path()
    if not ffmpeg_path:
        return jsonify({'error': 'FFmpeg не установлен или не найден'}), 500

    try:
        subprocess.run([ffmpeg_path, '-version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return jsonify({'error': 'FFmpeg не работает'}), 500

    download_dir = 'downloads'
    if not os.path.exists(download_dir):
        os.makedirs(download_dir)

    output_template = f'{download_dir}/%(title)s.%(ext)s'
    cookies = get_browser_cookies()

    ydl_opts = {
        'outtmpl': output_template,
        'noplaylist': True,
        'ffmpeg_location': ffmpeg_path,
        'cookies': cookies,
        'user_agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
        'progress_hooks': [lambda d: progress_hook(d, download_id)],
    }

    if format == 'mp4':
        ydl_opts.update({
            'format': 'bestvideo[vcodec^=avc1][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]',
            'merge_output_format': 'mp4',
            'postprocessors': [{
                'key': 'FFmpegVideoRemuxer',
                'preferedformat': 'mp4',
            }],
        })
    elif format == 'mp3':
        ydl_opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        })
    else:
        return jsonify({'error': 'Неподдерживаемый формат'}), 400

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get('title', 'unknown').replace('/', '_').replace('\\', '_')
            file_path = f'{download_dir}/{title}.{format}'
        
        if not os.path.exists(file_path):
            for f in os.listdir(download_dir):
                if f.endswith(f'.{format}'):
                    file_path = f'{download_dir}/{f}'
                    title = f[:-4]
                    break
            else:
                raise FileNotFoundError(f"Файл с расширением .{format} не найден")

        return jsonify({
            'status': 'success',
            'download_id': download_id,
            'filename': f'{title}.{format}',
            'file_path': file_path
        })

    except Exception as e:
        shutil.rmtree(download_dir, ignore_errors=True)
        if download_id in download_progress:
            del download_progress[download_id]
        return jsonify({'error': f'Ошибка: {str(e)}'}), 500

@app.route('/api/get_file/<download_id>', methods=['GET'])
def get_file(download_id):
    download_dir = 'downloads'
    for f in os.listdir(download_dir):
        if f.endswith('.mp4') or f.endswith('.mp3'):
            file_path = f'{download_dir}/{f}'
            title = f[:-4]
            response = send_file(file_path, as_attachment=True, download_name=f'{title}{f[-4:]}')
            shutil.rmtree(download_dir, ignore_errors=True)
            if download_id in download_progress:
                del download_progress[download_id]
            return response
    return jsonify({'error': 'Файл не найден'}), 404

@app.route('/api/extract_image', methods=['POST', 'OPTIONS'])
def api_extract_image():
    if request.method == 'OPTIONS':
        response = jsonify({'status': 'ok'})
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, ngrok-skip-browser-warning'
        return response

    data = request.get_json()
    url = data.get('url')
    if not url:
        return jsonify({'error': 'URL не указан'}), 400

    thumbnail = None
    cookies = get_browser_cookies()
    
    ydl_opts = {
        'noplaylist': True,
        'cookies': cookies,
        'user_agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
        'skip_download': True,
        'quiet': True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            thumbnail = info.get('thumbnail', None)
    except Exception as e:
        logger.info(f"yt-dlp не смог обработать URL: {str(e)}")

    if not thumbnail or 'instagram.com' in url or 'facebook.com' in url:
        proxies = [
            f"https://api.allorigins.win/raw?url={url}",
            f"https://corsproxy.io/?{url}",
            f"https://api.codetabs.com/v1/proxy?quest={url}",
            url
        ]
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8'
        }

        for proxy_url in proxies:
            try:
                response = requests.get(proxy_url, headers=headers, timeout=10)
                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, 'html.parser')
                    thumbnail = (
                        soup.find('meta', property='og:image') or
                        soup.find('meta', property='og:image:secure_url') or
                        soup.find('meta', {'name': 'twitter:image'})
                    )
                    thumbnail = thumbnail.get('content') if thumbnail else None
                    if thumbnail:
                        if not thumbnail.startswith('http'):
                            thumbnail = f"https://{url.split('/')[2]}{thumbnail}"
                        break
            except Exception as e:
                logger.info(f"Прокси {proxy_url} не сработал: {str(e)}")
                continue

    if not thumbnail:
        return jsonify({'error': 'Не удалось найти изображение'}), 404

    return jsonify({
        'status': 'success',
        'thumbnail': thumbnail
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)