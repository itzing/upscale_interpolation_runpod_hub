import base64
import binascii
import json
import logging
import os
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
import uuid

import cv2
import runpod
import websocket
from PIL import Image
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


REQUEST_AAD = b'engui:upscale-interpolation:v1'
IMAGE_RESULT_AAD = b'engui:upscale-interpolation:image-result:v1'
VIDEO_RESULT_AAD = b'engui:upscale-interpolation:video-result:v1'
RUNTIME_ROOT = os.getenv('RUNTIME_ROOT', '/dev/shm/comfy-runtime')
INPUT_DIR = os.getenv('INPUT_DIR', os.path.join(RUNTIME_ROOT, 'input'))
OUTPUT_DIR = os.getenv('OUTPUT_DIR', os.path.join(RUNTIME_ROOT, 'output'))
TEMP_DIR = os.getenv('TEMP_DIR', os.path.join(RUNTIME_ROOT, 'temp'))


def check_cuda_availability():
    try:
        import torch

        if torch.cuda.is_available():
            logger.info('✅ CUDA is available and working')
            os.environ['CUDA_VISIBLE_DEVICES'] = '0'
            return True

        logger.error('❌ CUDA is not available')
        raise RuntimeError('CUDA is required but not available')
    except Exception as error:
        logger.error(f'❌ CUDA check failed: {error}')
        raise RuntimeError(f'CUDA initialization failed: {error}')


try:
    cuda_available = check_cuda_availability()
    if not cuda_available:
        raise RuntimeError('CUDA is not available')
except Exception as error:
    logger.error(f'Fatal error: {error}')
    logger.error('Exiting due to CUDA requirements not met')
    exit(1)


server_address = os.getenv('SERVER_ADDRESS', '127.0.0.1')
client_id = str(uuid.uuid4())


def mask_job_input_for_log(job_input):
    masked = dict(job_input)

    for key in ['image_base64', 'video_base64', 'image_url', 'video_url']:
        if key in masked:
            masked[key] = '[REDACTED]'

    if '_secure' in masked:
        secure = masked.get('_secure') or {}
        masked['_secure'] = {
            'v': secure.get('v'),
            'alg': secure.get('alg'),
            'kid': secure.get('kid'),
            'ts': secure.get('ts'),
            'nonce': '[REDACTED]',
            'ciphertext': '[REDACTED]',
        }

    return masked


def decode_encryption_key():
    key_b64 = os.getenv('UPSCALE_FIELD_ENC_KEY_B64') or os.getenv('FIELD_ENC_KEY_B64')
    if not key_b64:
        return None

    try:
        key = base64.b64decode(key_b64)
    except Exception as error:
        raise Exception(f'Invalid encryption key encoding: {error}')

    if len(key) != 32:
        raise Exception(f'Invalid encryption key length: expected 32 bytes, got {len(key)}')

    return key


def decrypt_secure_input(job_input):
    secure = job_input.get('_secure')
    if not secure:
        return job_input

    key = decode_encryption_key()
    if not key:
        raise Exception('Secure payload received but UPSCALE_FIELD_ENC_KEY_B64 is missing')

    try:
        nonce = base64.b64decode(secure['nonce'])
        ciphertext = base64.b64decode(secure['ciphertext'])
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, REQUEST_AAD)
        payload = json.loads(plaintext.decode('utf-8'))
    except Exception as error:
        raise Exception(f'Failed to decrypt secure payload: {error}')

    for key_name, value in payload.items():
        job_input[key_name] = value

    job_input.pop('_secure', None)
    return job_input


def encrypt_output_base64(media_data_base64, aad, mime, default_kid='upscale-k1'):
    key = decode_encryption_key()
    if not key:
        return None

    try:
        media_bytes = base64.b64decode(media_data_base64)
    except Exception as error:
        raise Exception(f'Failed to decode media bytes for encryption: {error}')

    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, media_bytes, aad)

    return {
        'v': 1,
        'alg': 'AES-256-GCM',
        'kid': os.getenv('UPSCALE_FIELD_ENC_KID', default_kid),
        'nonce': base64.b64encode(nonce).decode('utf-8'),
        'ciphertext': base64.b64encode(ciphertext).decode('utf-8'),
        'mime': mime,
    }


def queue_prompt(prompt):
    url = f'http://{server_address}:8188/prompt'
    logger.info(f'Queueing prompt to: {url}')
    payload = {'prompt': prompt, 'client_id': client_id}
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data)
    return json.loads(urllib.request.urlopen(req).read())


def get_image(filename, subfolder, folder_type):
    url = f'http://{server_address}:8188/view'
    logger.info(f'Getting image from: {url}')
    data = {'filename': filename, 'subfolder': subfolder, 'type': folder_type}
    url_values = urllib.parse.urlencode(data)
    with urllib.request.urlopen(f'{url}?{url_values}') as response:
        return response.read()


def get_history(prompt_id):
    url = f'http://{server_address}:8188/history/{prompt_id}'
    logger.info(f'Getting history from: {url}')
    with urllib.request.urlopen(url) as response:
        return json.loads(response.read())


def wait_for_prompt_completion(ws, prompt):
    prompt_id = queue_prompt(prompt)['prompt_id']

    while True:
        out = ws.recv()
        if not isinstance(out, str):
            continue

        message = json.loads(out)
        if message.get('type') != 'executing':
            continue

        data = message.get('data', {})
        if data.get('node') is None and data.get('prompt_id') == prompt_id:
            return prompt_id


def get_video_path(ws, prompt):
    prompt_id = wait_for_prompt_completion(ws, prompt)
    history = get_history(prompt_id)[prompt_id]

    for node_id in history['outputs']:
        node_output = history['outputs'][node_id]
        if 'gifs' in node_output:
            for video in node_output['gifs']:
                return video['fullpath'], prompt_id

    return None, prompt_id


def get_image_path(ws, prompt):
    prompt_id = wait_for_prompt_completion(ws, prompt)
    history = get_history(prompt_id)[prompt_id]

    for node_id in history['outputs']:
        node_output = history['outputs'][node_id]
        if 'images' in node_output:
            for image in node_output['images']:
                filename = image['filename']
                subfolder = image.get('subfolder', '')
                if subfolder:
                    full_path = os.path.join(OUTPUT_DIR, subfolder, filename)
                else:
                    full_path = os.path.join(OUTPUT_DIR, filename)
                return full_path, prompt_id

    return None, prompt_id


def get_image_dimensions(image_path):
    try:
        with Image.open(image_path) as img:
            width, height = img.size
            return width, height
    except Exception as error:
        logger.error(f'Failed to read image dimensions: {error}')
        raise


def get_video_dimensions(video_path):
    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise ValueError(f'Cannot open video: {video_path}')
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()
        return width, height
    except Exception as error:
        logger.error(f'Failed to read video dimensions: {error}')
        raise


def get_video_fps(video_path):
    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise ValueError(f'Cannot open video: {video_path}')
        fps = cap.get(cv2.CAP_PROP_FPS)
        cap.release()
        return fps
    except Exception as error:
        logger.error(f'Failed to read video FPS: {error}')
        raise


def calculate_resolution(width, height):
    min_dimension = min(width, height)
    resolution = min_dimension * 2
    logger.info(f'Input size: {width}x{height}, min dimension: {min_dimension}, computed resolution: {resolution}')
    return resolution


def load_workflow(workflow_path):
    with open(workflow_path, 'r') as file:
        return json.load(file)


def ensure_http_ready():
    http_url = f'http://{server_address}:8188/'
    logger.info(f'Checking HTTP connection to: {http_url}')

    max_http_attempts = 180
    for http_attempt in range(max_http_attempts):
        try:
            urllib.request.urlopen(http_url, timeout=5)
            logger.info(f'HTTP connection ready on attempt {http_attempt + 1}')
            return
        except Exception as error:
            logger.warning(f'HTTP connection failed ({http_attempt + 1}/{max_http_attempts}): {error}')
            if http_attempt == max_http_attempts - 1:
                raise Exception('Cannot connect to ComfyUI HTTP server')
            time.sleep(1)


def connect_websocket():
    ws_url = f'ws://{server_address}:8188/ws?clientId={client_id}'
    logger.info(f'Connecting to WebSocket: {ws_url}')

    ws = websocket.WebSocket()
    max_attempts = int(180 / 5)
    for attempt in range(max_attempts):
        try:
            ws.connect(ws_url)
            logger.info(f'WebSocket connection ready on attempt {attempt + 1}')
            return ws
        except Exception as error:
            logger.warning(f'WebSocket connection failed ({attempt + 1}/{max_attempts}): {error}')
            if attempt == max_attempts - 1:
                raise Exception('WebSocket connection timeout (3 minutes)')
            time.sleep(5)


def cleanup_path(path_value):
    if not path_value or not os.path.exists(path_value):
        return

    try:
        if os.path.isdir(path_value):
            shutil.rmtree(path_value, ignore_errors=True)
        else:
            os.remove(path_value)
        logger.info(f'Cleaned up: {path_value}')
    except Exception as error:
        logger.warning(f'Cleanup skipped for {path_value}: {error}')


def cleanup_runtime_artifacts(task_id):
    paths_to_clean = [
        os.path.abspath(task_id),
        INPUT_DIR,
        OUTPUT_DIR,
        TEMP_DIR,
        '/ComfyUI/input',
        '/ComfyUI/output',
        '/ComfyUI/temp',
    ]

    for target in paths_to_clean:
        try:
            if not os.path.exists(target):
                continue

            if target in [INPUT_DIR, OUTPUT_DIR, TEMP_DIR, '/ComfyUI/input', '/ComfyUI/output', '/ComfyUI/temp']:
                for name in os.listdir(target):
                    path = os.path.join(target, name)
                    if os.path.isdir(path):
                        shutil.rmtree(path, ignore_errors=True)
                    else:
                        try:
                            os.remove(path)
                        except FileNotFoundError:
                            pass
            else:
                if os.path.isdir(target):
                    shutil.rmtree(target, ignore_errors=True)
                elif os.path.isfile(target):
                    os.remove(target)
        except Exception as cleanup_error:
            logger.warning(f'Cleanup warning for {target}: {cleanup_error}')


def collect_db_snapshot():
    db_paths = {
        'runtime': os.path.join(RUNTIME_ROOT, 'user', 'comfyui.db'),
        'legacy': '/ComfyUI/user/comfyui.db',
    }

    snapshot = {}
    for key, db_path in db_paths.items():
        try:
            if os.path.exists(db_path):
                snapshot[key] = {
                    'exists': True,
                    'size': os.path.getsize(db_path),
                }
            else:
                snapshot[key] = {
                    'exists': False,
                    'size': 0,
                }
        except Exception as error:
            snapshot[key] = {'error': error.__class__.__name__}

    return snapshot


def diff_db_snapshot(before, after):
    output = {}
    for key in set(before.keys()) | set(after.keys()):
        before_item = before.get(key, {})
        after_item = after.get(key, {})
        output[key] = {
            'before': before_item,
            'after': after_item,
        }
        if isinstance(before_item, dict) and isinstance(after_item, dict) and 'size' in before_item and 'size' in after_item:
            output[key]['size_delta'] = after_item['size'] - before_item['size']
    return output


def collect_cleanup_state():
    state = {
        'history_count': None,
        'files': {},
        'db': {},
    }

    try:
        url = f'http://{server_address}:8188/history'
        with urllib.request.urlopen(url, timeout=5) as response:
            history = json.loads(response.read())
        state['history_count'] = len(history) if isinstance(history, dict) else -1
    except Exception as error:
        state['history_count'] = f'error:{error.__class__.__name__}'

    for folder_name, path in [('input', INPUT_DIR), ('output', OUTPUT_DIR), ('temp', TEMP_DIR)]:
        try:
            state['files'][folder_name] = len(os.listdir(path)) if os.path.isdir(path) else 'missing'
        except Exception as error:
            state['files'][folder_name] = f'error:{error.__class__.__name__}'

    for key, db_path in {
        'runtime': os.path.join(RUNTIME_ROOT, 'user', 'comfyui.db'),
        'legacy': '/ComfyUI/user/comfyui.db',
    }.items():
        try:
            if os.path.exists(db_path):
                state['db'][key] = {'exists': True, 'size': os.path.getsize(db_path)}
            else:
                state['db'][key] = {'exists': False}
        except Exception as error:
            state['db'][key] = {'error': error.__class__.__name__}

    return state


def handler(job):
    raw_job_input = job.get('input', {})
    logger.info(f'Received job input (masked): {mask_job_input_for_log(raw_job_input)}')

    task_id = f'task_{uuid.uuid4()}'
    os.makedirs(task_id, exist_ok=True)

    comfyui_input_path = None
    result_path = None
    prompt_id = None
    ws = None
    db_snapshot_before = collect_db_snapshot()

    try:
        job_input = decrypt_secure_input(dict(raw_job_input))

        output_format = job_input.get('output', 'file_path')

        image_path_input = job_input.get('image_path')
        image_url_input = job_input.get('image_url')
        image_base64_input = job_input.get('image_base64')

        video_path_input = job_input.get('video_path')
        video_url_input = job_input.get('video_url')
        video_base64_input = job_input.get('video_base64')

        input_path = None
        input_type = None
        task_type = None

        if image_path_input or image_url_input or image_base64_input:
            input_type = 'image'
            task_type = 'image_upscale'

            if image_path_input:
                input_path = image_path_input
            elif image_url_input:
                try:
                    parsed_url = urllib.parse.urlparse(image_url_input)
                    path = parsed_url.path
                    ext = os.path.splitext(path)[1] or '.png'
                    input_path = os.path.join(task_id, f'input_image{ext}')
                    urllib.request.urlretrieve(image_url_input, input_path)
                    logger.info('Downloaded image from URL')
                except Exception as error:
                    return {'error': f'Failed to download image URL: {error}'}
            elif image_base64_input:
                try:
                    decoded_data = base64.b64decode(image_base64_input)
                    from io import BytesIO
                    img = Image.open(BytesIO(decoded_data))
                    img_format = img.format.lower() if img.format else 'png'
                    ext = f'.{img_format}'
                    input_path = os.path.join(task_id, f'input_image{ext}')
                    with open(input_path, 'wb') as file:
                        file.write(decoded_data)
                    logger.info(f'Saved base64 image to {input_path}')
                except Exception:
                    input_path = os.path.join(task_id, 'input_image.png')
                    with open(input_path, 'wb') as file:
                        file.write(base64.b64decode(image_base64_input))
                    logger.info(f'Saved base64 image to {input_path} using fallback extension')

        elif video_path_input or video_url_input or video_base64_input:
            input_type = 'video'
            task_type_input = job_input.get('task_type', 'upscale')
            task_type = 'video_upscale_and_interpolation' if task_type_input == 'upscale_and_interpolation' else 'video_upscale'

            if video_path_input:
                input_path = video_path_input
            elif video_url_input:
                try:
                    input_path = os.path.join(task_id, 'input_video.mp4')
                    urllib.request.urlretrieve(video_url_input, input_path)
                    logger.info('Downloaded video from URL')
                except Exception as error:
                    return {'error': f'Failed to download video URL: {error}'}
            elif video_base64_input:
                try:
                    input_path = os.path.join(task_id, 'input_video.mp4')
                    decoded_data = base64.b64decode(video_base64_input)
                    with open(input_path, 'wb') as file:
                        file.write(decoded_data)
                    logger.info(f'Saved base64 video to {input_path}')
                except Exception as error:
                    return {'error': f'Failed to decode base64 video: {error}'}
        else:
            return {'error': 'Missing input. Provide image_path/image_url/image_base64 or video_path/video_url/video_base64'}

        video_fps = None
        try:
            if input_type == 'image':
                width, height = get_image_dimensions(input_path)
            else:
                width, height = get_video_dimensions(input_path)
                if task_type == 'video_upscale_and_interpolation':
                    video_fps = get_video_fps(input_path)
                    logger.info(f'Original video FPS: {video_fps}')
            resolution = calculate_resolution(width, height)
        except Exception as error:
            return {'error': f'Failed to inspect input dimensions: {error}'}

        workflow_dir = os.path.join(os.path.dirname(__file__), 'workflow')

        if task_type == 'image_upscale':
            workflow_path = os.path.join(workflow_dir, 'image_upscale.json')
            prompt = load_workflow(workflow_path)
            prompt['16']['inputs']['image'] = os.path.basename(input_path)
            prompt['10']['inputs']['resolution'] = resolution
        elif task_type == 'video_upscale':
            workflow_path = os.path.join(workflow_dir, 'video_upscale_api.json')
            prompt = load_workflow(workflow_path)
            prompt['21']['inputs']['file'] = os.path.basename(input_path)
            prompt['10']['inputs']['resolution'] = resolution
        elif task_type == 'video_upscale_and_interpolation':
            workflow_path = os.path.join(workflow_dir, 'video_upscale_interpolation_api.json')
            prompt = load_workflow(workflow_path)
            prompt['21']['inputs']['file'] = os.path.basename(input_path)
            prompt['10']['inputs']['resolution'] = resolution
            if video_fps is not None:
                doubled_fps = video_fps * 2
                prompt['25']['inputs']['frame_rate'] = doubled_fps
                logger.info(f'Set Video Combine FPS to {doubled_fps}')
            else:
                logger.warning('Could not measure FPS, using workflow default')
        else:
            return {'error': f'Unsupported task type: {task_type}'}

        comfyui_input_dir = INPUT_DIR
        os.makedirs(comfyui_input_dir, exist_ok=True)
        input_filename = os.path.basename(input_path)
        comfyui_input_path = os.path.join(comfyui_input_dir, input_filename)
        shutil.copy2(input_path, comfyui_input_path)
        logger.info(f'Copied input file to ComfyUI input dir: {comfyui_input_path}')

        ensure_http_ready()
        ws = connect_websocket()

        if input_type == 'image':
            result_path, prompt_id = get_image_path(ws, prompt)
            result_key = 'image_path'
            result_base64_key = 'image'
        else:
            result_path, prompt_id = get_video_path(ws, prompt)
            result_key = 'video_path'
            result_base64_key = 'video'

        if not result_path:
            return {'error': f'Failed to create {input_type} output'}

        if output_format == 'base64':
            try:
                with open(result_path, 'rb') as file:
                    result_data = base64.b64encode(file.read()).decode('utf-8')

                if input_type == 'image':
                    encrypted = encrypt_output_base64(result_data, IMAGE_RESULT_AAD, 'image/png')
                    if encrypted:
                        logger.info('Returning encrypted image payload')
                        return {'image_encrypted': encrypted}
                else:
                    encrypted = encrypt_output_base64(result_data, VIDEO_RESULT_AAD, 'video/mp4')
                    if encrypted:
                        logger.info('Returning encrypted video payload')
                        return {'video_encrypted': encrypted}

                logger.warning('Encryption key not configured, falling back to plaintext base64 output')
                return {result_base64_key: result_data}
            except Exception as error:
                logger.error(f'Failed to encode {input_type} result: {error}')
                return {'error': f'Failed to encode {input_type} result: {error}'}

        logger.info(f'Original {input_type} result path: {result_path}')
        try:
            runpod_volume_dir = '/runpod-volume'
            os.makedirs(runpod_volume_dir, exist_ok=True)

            original_filename = os.path.basename(result_path)
            file_ext = os.path.splitext(original_filename)[1]
            output_filename = f'upscale_{task_id}{file_ext}'
            output_path = os.path.join(runpod_volume_dir, output_filename)

            logger.info(f'Copying result to runpod-volume: {result_path} -> {output_path}')
            shutil.copy2(result_path, output_path)

            if os.path.exists(output_path):
                file_size = os.path.getsize(output_path)
                logger.info(f'✅ Copied result to {output_path} ({file_size} bytes)')
                return {result_key: output_path}

            logger.error(f'Result copy failed, file does not exist: {output_path}')
            return {'error': 'Failed to copy result file'}
        except Exception as error:
            logger.error(f'Failed to copy result to runpod-volume: {error}')
            logger.warning(f'Falling back to original path: {result_path}')
            return {result_key: result_path}

    except Exception as error:
        logger.exception('Upscale handler failed')
        return {'error': str(error)}
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

        try:
            cleanup_script = os.getenv('FINISH_CLEANUP_SCRIPT', '/scripts/finish_cleanup.sh')
            if os.path.exists(cleanup_script):
                subprocess.run([
                    cleanup_script,
                    prompt_id or ''
                ], check=False, env={**os.environ, 'COMFY_BASE_URL': f'http://{server_address}:8188'})
        except Exception as cleanup_script_error:
            logger.warning(f'Cleanup script warning: {cleanup_script_error}')

        cleanup_path(comfyui_input_path)

        if result_path and isinstance(result_path, str) and result_path.startswith(OUTPUT_DIR):
            cleanup_path(result_path)

        cleanup_runtime_artifacts(task_id)

        try:
            cleanup_state = collect_cleanup_state()
            db_snapshot_after = collect_db_snapshot()
            db_diff = diff_db_snapshot(db_snapshot_before, db_snapshot_after)
            logger.info(
                f"Cleanup verify: history={cleanup_state['history_count']}, "
                f"files={cleanup_state['files']}, db_diff={db_diff}"
            )
        except Exception as cleanup_verify_error:
            logger.warning(f'Cleanup verify warning: {cleanup_verify_error}')


runpod.serverless.start({'handler': handler})
