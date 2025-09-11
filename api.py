if __name__ == '__main__':
    print('API ...')
    import json
    import multiprocessing
    import random
    import re
    import shutil
    import threading
    import time
    from datetime import datetime
    from pathlib import Path

    from flask import Flask, request, jsonify, render_template_string, send_from_directory, redirect, url_for
    from waitress import serve
    from werkzeug.utils import secure_filename


    from videotrans.configure import config
    from videotrans.task._dubbing import DubbingSrt
    from videotrans.task._speech2text import SpeechToText
    from videotrans.task._translate_srt import TranslateSrt
    from videotrans.task.job import start_thread
    from videotrans.task.trans_create import TransCreate
    from videotrans.util import tools
    from videotrans import tts as tts_model, translator, recognition

    ###### 配置信息
    #### api文档 https://pyvideotrans.com/api-cn
    config.exec_mode='api'
    ROOT_DIR = config.ROOT_DIR
    HOST = "127.0.0.1"
    PORT = 9011
    if Path(ROOT_DIR+'/host.txt').is_file():
        host_str=Path(ROOT_DIR+'/host.txt').read_text(encoding='utf-8').strip()
        host_str=re.sub(r'https?://','',host_str).split(':')
        if len(host_str)>0:
            HOST=host_str[0]
        if len(host_str)==2:
            PORT=int(host_str[1])

    # 存储生成的文件和进度日志
    API_RESOURCE='apidata'
    TARGET_DIR = ROOT_DIR + f'/{API_RESOURCE}'
    Path(TARGET_DIR).mkdir(parents=True, exist_ok=True)
    # 进度日志
    PROCESS_INFO = TARGET_DIR + '/processinfo'
    if Path(PROCESS_INFO).is_dir():
        shutil.rmtree(PROCESS_INFO)
    Path(PROCESS_INFO).mkdir(parents=True, exist_ok=True)
    # url前缀
    URL_PREFIX = f"http://{HOST}:{PORT}/{API_RESOURCE}"
    config.exit_soft = False
    # 停止 结束 失败状态
    end_status_list = ['error', 'succeed', 'end', 'stop']
    #日志状态
    logs_status_list = ['logs']

    ######################

    app = Flask(__name__, static_folder=TARGET_DIR)

    # 根路径重定向到上传查看页
    @app.route('/', methods=['GET'])
    def index():
        return redirect('/viewer')

    # 直接提供 /apidata 静态访问，以便页面可直接访问上传的视频/字幕
    @app.route(f'/{API_RESOURCE}/<path:subpath>')
    def _serve_apidata(subpath):
        return send_from_directory(TARGET_DIR, subpath)

    # 简易网页：上传视频+SRT 并查看播放器、字幕列表和时间轴
    @app.route('/viewer', methods=['GET'])
    def viewer_home():
        html = """
        <!doctype html>
        <html lang="zh">
        <head>
            <meta charset="utf-8" />
            <meta name="viewport" content="width=device-width, initial-scale=1" />
            <title>字幕查看器</title>
            <style>
                body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, 'PingFang SC', 'Microsoft YaHei', sans-serif; }
                header { padding: 12px 16px; border-bottom: 1px solid #eee; }
                main { padding: 16px; }
                form { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
                .hint { color: #666; font-size: 12px; }
            </style>
        </head>
        <body>
            <header>
                <h3 style="margin:0;">上传视频与 SRT 字幕</h3>
                <div class="hint">SRT 第一行若包含 [说话人]，将解析为说话人</div>
            </header>
            <main>
                <form action="/upload_viewer" method="post" enctype="multipart/form-data">
                    <label>视频文件: <input type="file" name="video" accept="video/*,audio/*" required></label>
                    <label>SRT 文件: <input type="file" name="srt" accept=".srt" required></label>
                    <button type="submit">上传并查看</button>
                </form>
            </main>
        </body>
        </html>
        """
        return render_template_string(html)

    # FunASR 上传+识别（带说话人标签）页面
    @app.route('/funasr', methods=['GET'])
    def funasr_home():
        html = """
        <!doctype html>
        <html lang=\"zh\">
        <head>
            <meta charset=\"utf-8\" />
            <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
            <title>FunASR 说话人标注</title>
            <style>
                body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial; }
                header { padding: 12px 16px; border-bottom: 1px solid #eee; }
                main { padding: 16px; }
                form { display: grid; gap: 12px; max-width: 720px; }
                .row { display:flex; gap:12px; align-items:center; }
                .hint { color:#666; font-size:12px; }
                button { padding: 8px 14px; border:1px solid #ddd; background:#fff; border-radius:6px; cursor:pointer; }
            </style>
        </head>
        <body>
            <header>
                <h3 style=\"margin:0;\">FunASR 说话人标注</h3>
                <div class=\"hint\">上传视频，使用 FunASR 进行识别并生成含 [spkX] 的 SRT</div>
            </header>
            <main>
                <form action=\"/funasr_run\" method=\"post\" enctype=\"multipart/form-data\">
                    <div class=\"row\"><label>视频文件: <input type=\"file\" name=\"video\" accept=\"video/*,audio/*\" required></label></div>
                    <div class=\"row\"><label><input type=\"checkbox\" name=\"enable_spk\" checked> 启用说话人识别</label></div>
                    <div class=\"row\"><button type=\"submit\">开始识别</button></div>
                </form>
            </main>
        </body>
        </html>
        """
        return html

    @app.route('/funasr_run', methods=['POST'])
    def funasr_run():
        # 处理上传并提交到识别队列（FunASR + 说话人标签）
        if 'video' not in request.files:
            return jsonify({"code": 1, "msg": "未选择视频文件"}), 400
        file = request.files['video']
        if not file or not file.filename.strip():
            return jsonify({"code": 1, "msg": "未选择视频文件"}), 400

        # 保存到临时目录
        ext = Path(file.filename).suffix
        tmp_name = f"upload_{int(time.time())}_{random.randint(1,9999)}{ext}"
        tmp_path = Path(config.TEMP_DIR) / tmp_name
        file.save(tmp_path.as_posix())

        # 识别配置（FunASR）
        from videotrans import recognition
        cfg = {
            "recogn_type": recognition.FUNASR_CN,
            "split_type": 'all',
            "model_name": 'paraformer-zh',
            "is_cuda": False,
            "detect_language": 'auto'
        }

        # 输出与缓存目录
        obj = tools.format_video(tmp_path.as_posix(), None)
        obj['target_dir'] = TARGET_DIR + f'/{obj["uuid"]}'
        obj['cache_folder'] = config.TEMP_DIR + f'/{obj["uuid"]}'
        Path(obj['target_dir']).mkdir(parents=True, exist_ok=True)
        cfg.update(obj)

        # 将上传的视频副本放入目标目录，供 /view/<task_id> 页面使用
        try:
            import shutil as _shutil
            _shutil.copy2(tmp_path.as_posix(), (Path(obj['target_dir']) / Path(tmp_path).name).as_posix())
        except Exception:
            pass

        # 启用说话人识别（全局参数，FunASR 读取该值决定是否拼接 [spkX]）
        enable_spk = request.form.get('enable_spk') is not None
        if enable_spk:
            config.params['paraformer_spk'] = True

        config.box_recogn = 'ing'
        trk = SpeechToText(cfg=cfg)
        config.prepare_queue.append(trk)
        tools.set_process(text=f"Currently in queue No.{len(config.prepare_queue)}", uuid=obj['uuid'])
        
        # 在语音识别完成后，将SRT文件重命名为raw.srt
        def rename_srt_to_raw(task_id):
            import time
            max_wait = 300  # 最多等待5分钟
            wait_time = 0
            while wait_time < max_wait:
                task_dir = Path(TARGET_DIR) / task_id
                if task_dir.exists():
                    # 查找生成的SRT文件
                    srt_files = list(task_dir.glob("*.srt"))
                    if srt_files:
                        # 将第一个SRT文件重命名为raw.srt
                        srt_file = srt_files[0]
                        raw_srt_path = task_dir / "raw.srt"
                        if srt_file != raw_srt_path:
                            srt_file.rename(raw_srt_path)
                            print(f"SRT文件已重命名为: raw.srt")
                        break
                time.sleep(2)
                wait_time += 2
        
        # 启动后台任务重命名SRT文件
        import threading
        threading.Thread(target=rename_srt_to_raw, args=(obj['uuid'],), daemon=True).start()
        
        # 跳转到结果页（完成后再跳转到 /view/<task_id> 进行编辑）
        return redirect(url_for('funasr_result', task_id=obj['uuid']))

    @app.route('/funasr_result/<task_id>', methods=['GET'])
    def funasr_result(task_id):
        # 简单结果与状态轮询页
        html = """
        <!doctype html>
        <html lang=\"zh\">
        <head>
            <meta charset=\"utf-8\" />
            <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
            <title>FunASR 结果</title>
            <style>
                body { margin:0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial; }
                header { padding:12px 16px; border-bottom:1px solid #eee; }
                main { padding:16px; }
                .hint { color:#666; font-size:12px; }
                .files a { display:block; margin:6px 0; }
                pre { background:#f7f7f7; padding:8px; border-radius:6px; }
            </style>
        </head>
        <body>
            <header>
                <h3 style=\"margin:0;\">FunASR 识别结果</h3>
                <div class=\"hint\">任务: ((TASK_ID))</div>
            </header>
            <main>
                <div id=\"status\">查询中...</div>
                <div class=\"files\" id=\"files\"></div>
                <pre id=\"error\" style=\"display:none\"></pre>
            </main>
            <script>
            const taskId = ((TASK_ID_JSON));
            const statusEl = document.getElementById('status');
            const filesEl = document.getElementById('files');
            const errEl = document.getElementById('error');

            async function query() {
                try {
                    const res = await fetch(`/task_status?task_id=${taskId}`);
                    const data = await res.json();
                    if (data.code === -1) { statusEl.textContent = data.msg || '处理中...'; return; }
                    if (data.code === 0) {
                        statusEl.textContent = '完成';
                        const urls = (data.data && data.data.url) || [];
                        filesEl.innerHTML = '';
                        urls.forEach(u => { const a = document.createElement('a'); a.href = u; a.textContent = u; filesEl.appendChild(a); });
                        // 完成后跳转到 /view/<task_id>
                        setTimeout(() => { location.href = `/view/${taskId}`; }, 800);
                        return true;
                    }
                    errEl.style.display = 'block'; errEl.textContent = data.msg || '出错了';
                    return true;
                } catch (e) { errEl.style.display = 'block'; errEl.textContent = String(e); return true; }
            }
            (async () => {
                const done = await query();
                if (!done) return;
            })();
            setInterval(query, 1500);
            </script>
        </body>
        </html>
        """
        html = html.replace('((TASK_ID))', task_id)
        html = html.replace('((TASK_ID_JSON))', json.dumps(task_id))
        # 不使用模板引擎，直接返回
        return html

    @app.route('/upload_viewer', methods=['POST'])
    def upload_viewer():
        from uuid import uuid4

        if 'video' not in request.files or 'srt' not in request.files:
            return jsonify({"code": 1, "msg": "缺少文件：需要同时上传视频和SRT"}), 400
        video = request.files['video']
        srt = request.files['srt']
        if not video or video.filename.strip() == '':
            return jsonify({"code": 1, "msg": "视频文件未选择"}), 400
        if not srt or srt.filename.strip() == '':
            return jsonify({"code": 1, "msg": "SRT 文件未选择"}), 400

        task_id = uuid4().hex[:10]
        task_dir = Path(TARGET_DIR) / task_id
        task_dir.mkdir(parents=True, exist_ok=True)

        video_name = secure_filename(video.filename)
        srt_name = secure_filename(srt.filename)

        # 保存文件
        video_path = (task_dir / video_name).as_posix()
        srt_path = (task_dir / srt_name).as_posix()
        video.save(video_path)
        srt.save(srt_path)

        return redirect(url_for('viewer_page', task_id=task_id))

    @app.route('/view/<task_id>', methods=['GET'])
    def viewer_page(task_id):
        # 查找该任务目录下的视频和srt
        task_dir = Path(TARGET_DIR) / task_id
        if not task_dir.exists():
            return jsonify({"code": 1, "msg": "任务不存在"}), 404

        all_files = [f.name for f in task_dir.iterdir() if f.is_file()]
        # 允许的播放文件后缀
        from videotrans.configure import config as _cfg
        exts = set([e.lower() for e in _cfg.VIDEO_EXTS + _cfg.AUDIO_EXITS])
        video_name = ''
        srt_name = ''
        for name in all_files:
            lower = name.lower()
            if lower.endswith('.srt'):
                srt_name = name
            elif any(lower.endswith('.' + e) for e in exts):
                if not video_name:
                    video_name = name

        if not video_name or not srt_name:
            return jsonify({"code": 1, "msg": "任务文件缺失（需要视频与srt）"}), 400

        video_url = f'/{API_RESOURCE}/{task_id}/{video_name}'
        # 页面：左侧字幕列表，右侧播放器与时间轴
        html = """
        <!doctype html>
        <html lang=\"zh\">
        <head>
            <meta charset=\"utf-8\" />
            <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
            <title>字幕查看器 - ((TASK_ID))</title>
            <style>
                body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, 'PingFang SC', 'Microsoft YaHei', sans-serif; }}
                header {{ padding: 12px 16px; border-bottom: 1px solid #eee; display:flex; gap:12px; align-items:center; }}
                header .task {{ color:#666; font-size:12px; }}
                .container {{ display: grid; grid-template-columns: 360px 1fr; gap: 12px; height: calc(100vh - 60px); padding: 12px; box-sizing: border-box; }}
                .list {{ 
                    border: 1px solid #e5e5e5; 
                    border-radius: 8px; 
                    overflow: auto; 
                    padding: 8px; 
                    background: #fafafa;
                    max-height: 70vh;
                    width: 100%;
                    box-sizing: border-box;
                }}
                .item {{ 
                    padding: 8px 10px; 
                    border-radius: 6px; 
                    cursor: pointer; 
                    margin-bottom: 6px; 
                    border: 1px solid transparent;
                    transition: all 0.2s ease;
                    background: white;
                    width: 100%;
                    box-sizing: border-box;
                }}
                .item:hover {{ 
                    background: #f0f8ff; 
                    border-color: #007AFF;
                    transform: translateY(-1px);
                    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                }}
                .item.active {{ 
                    background: #e9f3ff; 
                    border-color: #007AFF;
                    box-shadow: 0 2px 8px rgba(0,122,255,0.2);
                }}
                .time {{ 
                    color: #666; 
                    font-size: 11px; 
                    font-weight: 500;
                    margin-bottom: 4px;
                    font-family: 'SF Mono', Monaco, 'Cascadia Code', 'Roboto Mono', Consolas, 'Courier New', monospace;
                }}
                .speakerSel {{ 
                    padding: 4px 8px; 
                    font-size: 11px; 
                    border: 1px solid #ddd;
                    border-radius: 4px;
                    background: white;
                    min-width: 80px;
                    flex-shrink: 0;
                }}
                .textEdit {{ 
                    width: 100%; 
                    box-sizing: border-box; 
                    resize: vertical; 
                    min-height: 40px; 
                    font-size: 13px; 
                    line-height: 1.4; 
                    padding: 6px 8px; 
                    border: 1px solid #ddd; 
                    border-radius: 4px;
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                    margin: 0;
                }}
                .textEdit:focus {{
                    outline: none;
                    border-color: #007AFF;
                    box-shadow: 0 0 0 2px rgba(0,122,255,0.1);
                }}
                
                /* 滚动条样式 */
                .list::-webkit-scrollbar {{
                    width: 6px;
                }}
                .list::-webkit-scrollbar-track {{
                    background: #f1f1f1;
                    border-radius: 3px;
                }}
                .list::-webkit-scrollbar-thumb {{
                    background: #c1c1c1;
                    border-radius: 3px;
                }}
                .list::-webkit-scrollbar-thumb:hover {{
                    background: #a8a8a8;
                }}
                
                /* 字幕项编号 */
                .item::before {{
                    content: attr(data-idx);
                    position: absolute;
                    left: -20px;
                    top: 6px;
                    font-size: 10px;
                    color: #999;
                    font-weight: 500;
                }}
                .item {{
                    position: relative;
                    padding-left: 24px;
                }}
                .player {{ display:flex; flex-direction: column; gap: 8px; }}
                .timeline-wrap {{ border:1px solid #e5e5e5; border-radius:8px; padding:8px; }}
                canvas {{ width: 100%; height: 120px; display:block; cursor: grab; }}
                .timeline-wrap:hover {{ border-color: #007AFF; }}
                canvas:active {{ cursor: grabbing; }}
                .drag-hint {{ 
                    position: absolute; 
                    background: rgba(0,0,0,0.8); 
                    color: white; 
                    padding: 4px 8px; 
                    border-radius: 4px; 
                    font-size: 12px; 
                    pointer-events: none; 
                    z-index: 1000;
                    display: none;
                }}
                /* 合成等待弹窗 */
                .modal-overlay {{
                    position: fixed;
                    top: 0; left: 0; right: 0; bottom: 0;
                    background: rgba(0,0,0,0.4);
                    display: none;
                    align-items: center;
                    justify-content: center;
                    z-index: 2000;
                }}
                .modal {{
                    background: #fff;
                    width: 380px;
                    border-radius: 10px;
                    box-shadow: 0 10px 30px rgba(0,0,0,0.2);
                    padding: 16px 18px;
                    text-align: center;
                }}
                .modal h4 {{ margin: 8px 0 6px; font-size: 16px; }}
                .modal p {{ margin: 6px 0 0; color: #555; font-size: 13px; min-height: 18px; }}
                .spinner {{
                    width: 28px; height: 28px;
                    border: 3px solid #eee; border-top-color: #007AFF;
                    border-radius: 50%;
                    margin: 6px auto 4px;
                    animation: spin 0.8s linear infinite;
                }}
                @keyframes spin {{
                    to {{ transform: rotate(360deg); }}
                }}
            </style>
        </head>
        <body>
            <header>
                <h3 style=\"margin:0;\">字幕查看器</h3>
                <div class=\"task\">任务: {task_id}</div>
                <div style=\"margin-left:auto; display:flex; gap:8px; align-items:center;\">
                    <button id=\"btnTranslateSubtitle\" style=\"padding:6px 12px; border:1px solid #9c27b0; background:#9c27b0; color:#fff; border-radius:6px; cursor:pointer;\">1.翻译字幕</button>
                    <button id=\"btnSaveTranslation\" style=\"padding:6px 12px; border:1px solid #17a2b8; background:#17a2b8; color:#fff; border-radius:6px; cursor:pointer; display:none;\">保存翻译</button>
                    <button id=\"btnVoiceClone\" style=\"padding:6px 12px; border:1px solid #e91e63; background:#e91e63; color:#fff; border-radius:6px; cursor:pointer;\">语音克隆</button>
                    <button id=\"btnGenerateAudio\" style=\"padding:6px 12px; border:1px solid #ff9800; background:#ff9800; color:#fff; border-radius:6px; cursor:pointer; display:none;\">生成音频</button>
                    <button id=\"btnSynthesizeAudio\" style=\"padding:6px 12px; border:1px solid #4caf50; background:#4caf50; color:#fff; border-radius:6px; cursor:pointer; display:none;\">合成音频</button>
                    <button id=\"btnSynthesizeVideo\" style=\"padding:6px 12px; border:1px solid #ff6b35; background:#ff6b35; color:#fff; border-radius:6px; cursor:pointer;\">合成视频</button>
                    <button id=\"btnAddSubtitles\" style=\"padding:6px 12px; border:1px solid #007AFF; background:#007AFF; color:#fff; border-radius:6px; cursor:pointer;\">添加字幕</button>
                    <button id=\"btnSaveSrt\" style=\"padding:6px 12px; border:1px solid #ddd; background:#fff; border-radius:6px; cursor:pointer;\">下载SRT(含spk)</button>
                    <button id="btnSaveJson" style="padding:6px 12px; border:1px solid #ddd; background:#fff; border-radius:6px; cursor:pointer;">下载JSON</button>
                </div>
            </header>
            <div class=\"container\">
                <div class=\"list\" id=\"subList\"></div>
                <div class=\"player\">
                    <video id=\"video\" src=\"((VIDEO_URL))\" controls crossorigin=\"anonymous\" style=\"width:100%;max-height:60vh;background:#000\"></video>
                    <div class=\"timeline-wrap\" style=\"position: relative;\">
                        <div style=\"display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;\">
                            <div style=\"font-size: 12px; color: #666;\">
                                缩放: <span id=\"zoomLevel\">1x</span> | 
                                可见范围: <span id=\"visibleRange\">0s - 0s</span> | 
                                <span id=\"saveStatus\" style=\"color: #28a745;\">已保存</span> | 
                                快捷键: R=重置 F=适应 ←→=平移 | 拖拽空白区域滑动
                            </div>
                            <div id=\"speakerLegend\" style=\"display: flex; gap: 8px; font-size: 11px;\"></div>
                            <div>
                                <button id=\"zoomIn\" style=\"padding: 2px 8px; margin-right: 4px; font-size: 12px;\">放大</button>
                                <button id=\"zoomOut\" style=\"padding: 2px 8px; margin-right: 4px; font-size: 12px;\">缩小</button>
                                <button id=\"zoomReset\" style=\"padding: 2px 8px; margin-right: 8px; font-size: 12px;\">重置</button>
                                <button id=\"saveTimeline\" style=\"padding: 2px 8px; font-size: 12px; background: #28a745; color: white; border: none; border-radius: 3px;\">保存</button>
                            </div>
                        </div>
                        <canvas id=\"timeline\" width=\"1200\" height=\"120\"></canvas>
                        <div id=\"dragHint\" class=\"drag-hint\"></div>
                    </div>
                </div>
            </div>
            <!-- 合成视频等待弹窗 -->
            <div id="synthModal" class="modal-overlay">
              <div class="modal">
                <div class="spinner"></div>
                <h4>正在合成视频</h4>
                <p id="synthModalMsg">请稍候...</p>
              </div>
            </div>

            <script>
            const taskId = ((TASK_ID_JSON));
            const listEl = document.getElementById('subList');
            const videoEl = document.getElementById('video');
            const canvas = document.getElementById('timeline');
            const ctx = canvas.getContext('2d');
            const btnTranslateSubtitle = document.getElementById('btnTranslateSubtitle');
            const btnSaveTranslation = document.getElementById('btnSaveTranslation');
            const btnVoiceClone = document.getElementById('btnVoiceClone');
            const btnGenerateAudio = document.getElementById('btnGenerateAudio');
            const btnSynthesizeAudio = document.getElementById('btnSynthesizeAudio');
            const btnSynthesizeVideo = document.getElementById('btnSynthesizeVideo');
            const btnSaveSrt = document.getElementById('btnSaveSrt');
            const btnAddSubtitles = document.getElementById('btnAddSubtitles');
            const btnSaveJson = document.getElementById('btnSaveJson');
            const dragHint = document.getElementById('dragHint');
            const zoomLevelEl = document.getElementById('zoomLevel');
            const visibleRangeEl = document.getElementById('visibleRange');
            const saveStatusEl = document.getElementById('saveStatus');
            const speakerLegendEl = document.getElementById('speakerLegend');
            const zoomInBtn = document.getElementById('zoomIn');
            const zoomOutBtn = document.getElementById('zoomOut');
            const zoomResetBtn = document.getElementById('zoomReset');
            const saveTimelineBtn = document.getElementById('saveTimeline');
            const synthModal = document.getElementById('synthModal');
            const synthModalMsg = document.getElementById('synthModalMsg');
            let cues = [];
            let videoMs = 0;
            let speakers = [];
            let speakerColors = {}; // 存储说话人对应的颜色
            const originalVideoUrl = '((VIDEO_URL))';

            function showSynthModal(msg) {
                synthModalMsg.textContent = msg || '请稍候...';
                synthModal.style.display = 'flex';
            }
            function hideSynthModal() {
                synthModal.style.display = 'none';
            }

            // 添加字幕弹窗
            const addSubModal = document.createElement('div');
            addSubModal.className = 'modal-overlay';
            addSubModal.innerHTML = `
              <div class="modal">
                <h4>添加字幕到视频</h4>
                <div style="text-align:left;font-size:13px;line-height:1.9;">
                  <label><input type="radio" name="addTarget" value="original" checked> 原视频（初始载入的）</label><br>
                  <label><input type="radio" name="addTarget" value="current"> 当前播放视频（可能是合成结果）</label>
                </div>
                <div style="display:flex;gap:8px;justify-content:center;margin-top:8px;flex-wrap:wrap;">
                  <div>
                    <div style="font-size:12px;color:#666;text-align:left;">字体大小(px)</div>
                    <input id="subFontSize" type="number" min="12" max="120" value="72" style="width:120px;padding:6px;">
                  </div>
                  <div>
                    <div style="font-size:12px;color:#666;text-align:left;">距离底部(%)</div>
                    <input id="subBottomPct" type="number" min="0" max="40" value="20" style="width:120px;padding:6px;">
                  </div>
                  <div style="min-width:280px;">
                    <div style="font-size:12px;color:#666;text-align:left;">选择字幕文件（可选）</div>
                    <select id="subFileSelect" style="width:280px;padding:6px;">
                      <option value="">使用当前页面字幕（翻译优先）</option>
                    </select>
                  </div>
                </div>
                <div style="display:flex;gap:8px;justify-content:center;margin-top:12px;">
                  <button id="btnSubCancel" style="padding:6px 12px;">取消</button>
                  <button id="btnSubOk" style="padding:6px 12px;background:#007AFF;color:#fff;border:none;border-radius:6px;">确定</button>
                </div>
              </div>`;
            document.body.appendChild(addSubModal);
            
            async function populateSubtitleFiles() {
                try {
                    const res = await fetch(`/viewer_api/${taskId}/list_subtitle_files`);
                    const data = await res.json();
                    const sel = document.getElementById('subFileSelect');
                    if (data && data.code === 0 && Array.isArray(data.files)) {
                        // 清空保留第一个默认选项
                        sel.innerHTML = '<option value="">使用当前页面字幕（翻译优先）</option>';
                        data.files.forEach(f => {
                            const opt = document.createElement('option');
                            opt.value = f.url; // 传URL
                            opt.textContent = f.name;
                            sel.appendChild(opt);
                        });
                    }
                } catch (e) {
                    console.warn('获取字幕文件列表失败', e);
                }
            }
            async function showAddSubModal() { await populateSubtitleFiles(); addSubModal.style.display = 'flex'; }
            function hideAddSubModal() { addSubModal.style.display = 'none'; }
            
            addSubModal.addEventListener('click', (e) => { if (e.target === addSubModal) hideAddSubModal(); });
            addSubModal.querySelector('#btnSubCancel').addEventListener('click', hideAddSubModal);
            addSubModal.querySelector('#btnSubOk').addEventListener('click', async () => {
                try {
                    const target = (addSubModal.querySelector('input[name="addTarget"]:checked')||{}).value || 'original';
                    const fontSize = Number(document.getElementById('subFontSize').value) || 72;
                    const bottomPercent = Number(document.getElementById('subBottomPct').value) || 20;
                    const videoUrl = target === 'original' ? originalVideoUrl : (videoEl.currentSrc || videoEl.src);

                    if (!videoUrl) { alert('未找到目标视频'); return; }
                    if (!cues || cues.length === 0) { alert('没有字幕数据'); return; }

                    const payload = {
                        video_url: videoUrl,
                        font_size: fontSize,
                        bottom_percent: bottomPercent,
                        subtitles: cues.map((c, index) => ({
                            line: index + 1,
                            start_time: Number(c.start) || 0,
                            end_time: Number(c.end) || 0,
                            // 优先使用翻译文本，其次回退到原文
                            text: String((c.translated_text && c.translated_text.trim()) || (c.translation && c.translation.trim()) || c.text || '').trim()
                        })),
                        subtitle_file: (document.getElementById('subFileSelect')||{}).value || ''
                    };

                    showSynthModal('正在添加字幕，请稍候...');
                    const res = await fetch(`/viewer_api/${taskId}/add_subtitles`, {
                        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
                    });
                    const data = await res.json();
                    if (data && data.code === 0 && data.output_url) {
                        hideAddSubModal();
                        hideSynthModal();
                        videoEl.src = data.output_url;
                        try { videoEl.load(); videoEl.play(); } catch(e){}
                    } else {
                        hideSynthModal();
                        alert(data && data.msg ? data.msg : '添加字幕失败');
                    }
                } catch (err) {
                    hideSynthModal();
                    console.error(err);
                    alert('添加字幕失败');
                }
            });

            function fmtMs(ms) {
                const s = Math.floor(ms/1000); const hh = String(Math.floor(s/3600)).padStart(2,'0');
                const mm = String(Math.floor((s%3600)/60)).padStart(2,'0');
                const ss = String(s%60).padStart(2,'0');
                const mmm = String(ms%1000).padStart(3,'0');
                return `${hh}:${mm}:${ss},${mmm}`;
            }

            // 预定义的颜色调色板
            const colorPalette = [
                '#4e8cff', // 蓝色
                '#ff6b6b', // 红色
                '#4ecdc4', // 青色
                '#45b7d1', // 天蓝色
                '#96ceb4', // 绿色
                '#feca57', // 黄色
                '#ff9ff3', // 粉色
                '#54a0ff', // 亮蓝色
                '#5f27cd', // 紫色
                '#00d2d3', // 青绿色
                '#ff9f43', // 橙色
                '#10ac84', // 深绿色
                '#ee5a24', // 深橙色
                '#0984e3', // 深蓝色
                '#6c5ce7', // 紫罗兰
                '#a29bfe', // 淡紫色
                '#fd79a8', // 玫瑰色
                '#fdcb6e', // 淡黄色
                '#e17055', // 珊瑚色
                '#74b9ff'  // 浅蓝色
            ];

            // 为说话人分配颜色
            function assignSpeakerColor(speaker) {
                if (!speaker || speaker.trim() === '') return '#cddffd'; // 默认颜色
                
                if (speakerColors[speaker]) {
                    return speakerColors[speaker];
                }
                
                // 获取已使用的颜色
                const usedColors = Object.values(speakerColors);
                let colorIndex = 0;
                
                // 找到第一个未使用的颜色
                while (usedColors.includes(colorPalette[colorIndex]) && colorIndex < colorPalette.length - 1) {
                    colorIndex++;
                }
                
                const color = colorPalette[colorIndex];
                speakerColors[speaker] = color;
                return color;
            }

            function renderList() {
                listEl.innerHTML = '';
                cues.forEach((c, idx) => {
                    const item = document.createElement('div');
                    item.className = 'item';
                    item.dataset.idx = idx;
                    item.dataset.start = c.start;
                    item.dataset.end = c.end;
                    const t = document.createElement('div');
                    t.className = 'time';
                    t.textContent = `${c.startraw} → ${c.endraw}`;
                    const tx = document.createElement('div');
                    tx.style.width = '100%';
                    
                    // 说话人选择器行
                    const speakerRow = document.createElement('div');
                    speakerRow.style.display = 'flex';
                    speakerRow.style.alignItems = 'center';
                    speakerRow.style.marginBottom = '6px';
                    speakerRow.style.gap = '8px';
                    
                    const sel = document.createElement('select');
                    sel.className = 'speakerSel';
                    const spkSet = new Set(speakers || []);
                    if (c.speaker && !spkSet.has(c.speaker)) spkSet.add(c.speaker);
                    const optionList = Array.from(spkSet);
                    optionList.forEach(s => {
                        const opt = document.createElement('option');
                        opt.value = s; opt.textContent = s; sel.appendChild(opt);
                    });
                    sel.value = c.speaker || '';
                    sel.addEventListener('change', () => { 
                        c.speaker = sel.value; 
                        triggerAutoSave(); // 说话人修改时也触发自动保存
                    });
                    
                    speakerRow.appendChild(sel);
                    tx.appendChild(speakerRow);
                    
                    // 原语言文本框
                    const originalWrapper = document.createElement('div');
                    originalWrapper.style.marginBottom = '6px';
                    
                    const originalLabel = document.createElement('div');
                    originalLabel.style.fontSize = '11px';
                    originalLabel.style.fontWeight = '600';
                    originalLabel.style.color = '#495057';
                    originalLabel.style.marginBottom = '4px';
                    originalLabel.innerHTML = '📝 原语言:';
                    
                    const content = document.createElement('textarea');
                    content.className = 'textEdit';
                    content.value = c.text || '';
                    content.placeholder = '原语言文本...';
                    content.style.width = '100%';
                    content.addEventListener('input', () => { 
                        c.text = content.value; 
                        triggerAutoSave(); // 文本修改时也触发自动保存
                    });
                    
                    originalWrapper.appendChild(originalLabel);
                    originalWrapper.appendChild(content);
                    tx.appendChild(originalWrapper);
                    
                    // 翻译语言文本框
                    const translatedWrapper = document.createElement('div');
                    
                    const translatedLabel = document.createElement('div');
                    translatedLabel.style.fontSize = '11px';
                    translatedLabel.style.fontWeight = '600';
                    translatedLabel.style.color = '#6c757d';
                    translatedLabel.style.marginBottom = '4px';
                    translatedLabel.innerHTML = '🌐 翻译:';
                    
                    const translatedInput = document.createElement('textarea');
                    translatedInput.className = 'textEdit';
                    // 优先显示已加载的翻译文本，按优先级顺序
                    translatedInput.value = c.translated_text || 
                                          c.translated_text_en || 
                                          c.translated_text_es || 
                                          c.translated_text_fr || 
                                          c.translated_text_ja || 
                                          c.translated_text_zh || 
                                          c.translated_text_pt || 
                                          c.translated_text_th || '';
                    translatedInput.placeholder = '翻译文本...';
                    translatedInput.style.width = '100%';
                    translatedInput.addEventListener('input', () => { 
                        c.translated_text = translatedInput.value; 
                        saveTranslationText(); // 翻译文本修改时自动保存
                    });
                    
                    translatedWrapper.appendChild(translatedLabel);
                    translatedWrapper.appendChild(translatedInput);
                    tx.appendChild(translatedWrapper);
                    
                    item.appendChild(t); item.appendChild(tx);
                    item.addEventListener('click', () => {
                        videoEl.currentTime = (c.start || 0) / 1000;
                    });
                    listEl.appendChild(item);
                });
            }

            // 拖拽相关变量
            let isDragging = false;
            let dragType = null; // 'start', 'end', 'move', 'pan'
            let dragCueIndex = -1;
            let dragStartX = 0;
            let dragStartTime = 0;
            let originalStartTime = 0;
            let originalEndTime = 0;
            let originalPanOffset = 0; // 用于平移拖拽
            
            // 缩放相关变量
            let zoomLevel = 1;
            let panOffset = 0; // 水平偏移

            // 更新说话人图例
            function updateSpeakerLegend() {
                speakerLegendEl.innerHTML = '';
                const uniqueSpeakers = [...new Set(cues.map(c => c.speaker).filter(s => s && s.trim()))];
                
                uniqueSpeakers.forEach(speaker => {
                    const color = assignSpeakerColor(speaker);
                    const legendItem = document.createElement('div');
                    legendItem.style.display = 'flex';
                    legendItem.style.alignItems = 'center';
                    legendItem.style.gap = '4px';
                    
                    const colorBox = document.createElement('div');
                    colorBox.style.width = '12px';
                    colorBox.style.height = '12px';
                    colorBox.style.backgroundColor = color;
                    colorBox.style.borderRadius = '2px';
                    colorBox.style.border = '1px solid #ccc';
                    
                    const speakerLabel = document.createElement('span');
                    speakerLabel.textContent = speaker;
                    speakerLabel.style.color = '#666';
                    
                    legendItem.appendChild(colorBox);
                    legendItem.appendChild(speakerLabel);
                    speakerLegendEl.appendChild(legendItem);
                });
            }

            // 更新缩放和平移显示
            function updateZoomDisplay() {
                zoomLevelEl.textContent = zoomLevel.toFixed(1) + 'x';
                const visibleDuration = videoMs / zoomLevel;
                const startTime = panOffset;
                const endTime = Math.min(videoMs, startTime + visibleDuration);
                visibleRangeEl.textContent = `${fmtMs(startTime)} - ${fmtMs(endTime)}`;
            }

            function drawTimeline(currentMs=0) {
                const w = canvas.clientWidth; const h = canvas.height;
                if (canvas.width !== w) canvas.width = w;
                ctx.clearRect(0,0,canvas.width,canvas.height);
                ctx.fillStyle = '#fafafa';
                ctx.fillRect(0,0,canvas.width,canvas.height);
                
                const pad = 8; const barH = 24; const top = (canvas.height - barH)/2;
                
                // 计算可见时间范围
                const visibleDuration = videoMs / zoomLevel;
                const startTime = Math.max(0, panOffset);
                const endTime = Math.min(videoMs, startTime + visibleDuration);
                
                // 绘制时间刻度
                ctx.fillStyle = '#ddd';
                ctx.font = '10px Arial';
                ctx.textAlign = 'left';
                const tickInterval = Math.max(1000, Math.floor(visibleDuration / 10)); // 至少1秒间隔
                for (let time = Math.ceil(startTime / tickInterval) * tickInterval; time <= endTime; time += tickInterval) {
                    const x = pad + ((time - startTime) / visibleDuration) * (canvas.width - 2*pad);
                    ctx.fillRect(x, top + barH + 2, 1, 8);
                    ctx.fillText(fmtMs(time), x + 2, top + barH + 12);
                }
                
                cues.forEach((c, i) => {
                    // 检查字幕是否在可见范围内
                    if (c.end < startTime || c.start > endTime) return;
                    
                    const x = pad + ((c.start - startTime) / visibleDuration) * (canvas.width - 2*pad);
                    const wbar = Math.max(2, ((c.end - c.start) / visibleDuration) * (canvas.width - 2*pad));
                    
                    // 根据说话人分配颜色
                    const speakerColor = assignSpeakerColor(c.speaker);
                    const isActive = currentMs >= c.start && currentMs < c.end;
                    
                    // 绘制字幕块背景
                    ctx.fillStyle = isActive ? speakerColor : speakerColor + '80'; // 活跃时完全不透明，非活跃时半透明
                    ctx.fillRect(x, top, wbar, barH);
                    
                    // 绘制说话人标签（如果空间足够）
                    if (wbar > 60 && c.speaker) {
                        ctx.fillStyle = '#fff';
                        ctx.font = 'bold 10px Arial';
                        ctx.textAlign = 'left';
                        const speakerText = c.speaker.length > 6 ? c.speaker.substring(0, 6) + '...' : c.speaker;
                        ctx.fillText(speakerText, x + 4, top + 12);
                    }
                    
                    // 绘制拖拽手柄
                    if (wbar > 8) { // 只有当字幕块足够宽时才显示手柄
                        // 开始时间手柄
                        ctx.fillStyle = '#2c5aa0';
                        ctx.fillRect(x - 2, top - 2, 4, barH + 4);
                        
                        // 结束时间手柄
                        ctx.fillStyle = '#2c5aa0';
                        ctx.fillRect(x + wbar - 2, top - 2, 4, barH + 4);
                    }
                    
                    // 绘制字幕文本（如果空间足够）
                    if (wbar > 100) {
                        ctx.fillStyle = '#333';
                        ctx.font = '11px Arial';
                        ctx.textAlign = 'center';
                        const text = c.text.substring(0, Math.floor(wbar/10));
                        ctx.fillText(text, x + wbar/2, top + barH/2 + 6);
                    }
                });
                
                // 绘制当前播放位置
                if (currentMs >= startTime && currentMs <= endTime) {
                    const xnow = pad + ((currentMs - startTime) / visibleDuration) * (canvas.width - 2*pad);
                ctx.strokeStyle = '#ff3b30';
                    ctx.lineWidth = 2;
                ctx.beginPath();
                ctx.moveTo(xnow, 0); ctx.lineTo(xnow, canvas.height); ctx.stroke();
                    ctx.lineWidth = 1;
                }
                
                // 更新显示
                updateZoomDisplay();
                updateSpeakerLegend();
            }

            function updateActive(currentMs) {
                const items = listEl.querySelectorAll('.item');
                items.forEach(el => el.classList.remove('active'));
                for (let i=0;i<cues.length;i++) {
                    const c = cues[i];
                    if (currentMs>=c.start && currentMs<c.end) {
                        const el = listEl.querySelector(`.item[data-idx="${i}"]`);
                        if (el) { el.classList.add('active'); el.scrollIntoView({block:'nearest', behavior:'smooth'}); }
                        break;
                    }
                }
                drawTimeline(currentMs);
            }

            // 检测鼠标位置对应的字幕块和拖拽类型
            function getCueAtPosition(x, y) {
                const pad = 8; const barH = 24; const top = (canvas.height - barH)/2;
                const tolerance = 6; // 拖拽容差
                
                // 计算可见时间范围
                const visibleDuration = videoMs / zoomLevel;
                const startTime = Math.max(0, panOffset);
                const endTime = Math.min(videoMs, startTime + visibleDuration);
                
                for (let i = 0; i < cues.length; i++) {
                    const c = cues[i];
                    
                    // 检查字幕是否在可见范围内
                    if (c.end < startTime || c.start > endTime) continue;
                    
                    const cueX = pad + ((c.start - startTime) / visibleDuration) * (canvas.width - 2*pad);
                    const cueW = Math.max(2, ((c.end - c.start) / visibleDuration) * (canvas.width - 2*pad));
                    
                    if (y >= top - tolerance && y <= top + barH + tolerance) {
                        if (x >= cueX - tolerance && x <= cueX + tolerance) {
                            return { index: i, type: 'start' };
                        } else if (x >= cueX + cueW - tolerance && x <= cueX + cueW + tolerance) {
                            return { index: i, type: 'end' };
                        } else if (x >= cueX && x <= cueX + cueW) {
                            return { index: i, type: 'move' };
                        }
                    }
                }
                return null;
            }

            // 显示拖拽提示
            function showDragHint(x, y, text) {
                dragHint.textContent = text;
                dragHint.style.display = 'block';
                dragHint.style.left = (x + 10) + 'px';
                dragHint.style.top = (y - 30) + 'px';
            }

            // 隐藏拖拽提示
            function hideDragHint() {
                dragHint.style.display = 'none';
            }

            // 自动保存定时器
            let saveTimeout = null;
            let isSaving = false;

            // 更新保存状态显示
            function updateSaveStatus(status, color = '#28a745') {
                saveStatusEl.textContent = status;
                saveStatusEl.style.color = color;
            }

            // 保存字幕到SRT文件（通用函数）
            async function saveSubtitles(showStatus = true) {
                if (isSaving) return;
                
                try {
                    isSaving = true;
                    if (showStatus) {
                        updateSaveStatus('保存中...', '#ffc107');
                    }
                    
                    // 收集所有字幕数据，包括时间和文字
                    const payload = { 
                        subtitles: cues.map(c => ({
                            start: Number(c.start)||0,
                            end: Number(c.end)||0,
                            text: String(c.text||'').trim(),
                            speaker: String(c.speaker||'').trim(),
                        })), 
                        srt_with_spk: true 
                    };
                    
                    const res = await fetch(`/viewer_api/${taskId}/export_srt`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });
                    
                    const data = await res.json();
                    if (data && data.code === 0) {
                        if (showStatus) {
                            updateSaveStatus('已保存', '#28a745');
                            console.log('字幕已保存');
                        }
                        return true;
                    } else {
                        if (showStatus) {
                            updateSaveStatus('保存失败', '#dc3545');
                        }
                        console.error('保存失败:', data && data.msg ? data.msg : '未知错误');
                        return false;
                    }
                } catch (e) {
                    if (showStatus) {
                        updateSaveStatus('保存失败', '#dc3545');
                    }
                    console.error('保存失败:', e);
                    return false;
                } finally {
                    isSaving = false;
                }
            }

            // 自动保存字幕到SRT文件
            async function autoSaveSubtitles() {
                return await saveSubtitles(true);
            }

            // 保存翻译文本
            let translationSaveTimeout = null;
            async function saveTranslationText() {
                if (translationSaveTimeout) {
                    clearTimeout(translationSaveTimeout);
                }
                
                translationSaveTimeout = setTimeout(async () => {
                    try {
                        const payload = { 
                            subtitles: cues.map(c => ({
                                start: Number(c.start)||0,
                                end: Number(c.end)||0,
                                text: String(c.text||'').trim(),
                                speaker: String(c.speaker||'').trim(),
                                translated_text: String(c.translated_text||'').trim(),
                            })), 
                            target_language: 'en' // 默认英语，可以根据需要调整
                        };
                        
                        const res = await fetch(`/viewer_api/${taskId}/save_translation`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify(payload)
                        });
                        
                        const data = await res.json();
                        if (data && data.code === 0) {
                            console.log('翻译文本已保存');
                        } else {
                            console.error('保存翻译文本失败:', data && data.msg ? data.msg : '未知错误');
                        }
                    } catch (e) {
                        console.error('保存翻译文本失败:', e);
                    }
                }, 1000); // 1秒后自动保存
            }

            // 触发自动保存（防抖）
            function triggerAutoSave(immediate = false) {
                if (saveTimeout) {
                    clearTimeout(saveTimeout);
                }
                
                if (immediate) {
                    // 立即保存
                    autoSaveSubtitles();
                } else {
                    // 延迟保存
                    updateSaveStatus('有未保存的更改', '#ffc107');
                    saveTimeout = setTimeout(() => {
                        autoSaveSubtitles();
                    }, 1000); // 1秒后自动保存
                }
            }

            // 更新字幕时间
            function updateCueTime(cueIndex, newStart, newEnd) {
                if (cueIndex >= 0 && cueIndex < cues.length) {
                    const cue = cues[cueIndex];
                    cue.start = Math.max(0, newStart);
                    cue.end = Math.max(cue.start + 100, newEnd); // 最小100ms间隔
                    cue.startraw = fmtMs(cue.start);
                    cue.endraw = fmtMs(cue.end);
                    
                    // 更新列表显示
                    renderList();
                    drawTimeline(videoEl.currentTime * 1000);
                    
                    // 触发自动保存
                    triggerAutoSave();
                }
            }

            // 将屏幕坐标转换为时间
            function screenToTime(x) {
                const pad = 8;
                const visibleDuration = videoMs / zoomLevel;
                const startTime = Math.max(0, panOffset);
                const ratio = Math.min(1, Math.max(0, (x - pad) / (canvas.width - 2*pad)));
                return startTime + ratio * visibleDuration;
            }

            // 鼠标按下事件
            canvas.addEventListener('mousedown', (e) => {
                const rect = canvas.getBoundingClientRect();
                const x = e.clientX - rect.left;
                const y = e.clientY - rect.top;
                
                const hit = getCueAtPosition(x, y);
                if (hit) {
                    isDragging = true;
                    dragType = hit.type;
                    dragCueIndex = hit.index;
                    dragStartX = x;
                    
                    // 记录原始时间
                    const cue = cues[dragCueIndex];
                    originalStartTime = cue.start;
                    originalEndTime = cue.end;
                    
                    // 改变鼠标样式
                    canvas.style.cursor = 'ew-resize';
                    e.preventDefault();
                } else {
                    // 点击空白区域，定位播放线
                    const ms = screenToTime(x);
                    videoEl.currentTime = ms / 1000;
                    
                    // 如果按住鼠标，则开始平移拖拽
                    if (e.button === 0) { // 左键
                        isDragging = true;
                        dragType = 'pan';
                        dragStartX = x;
                        originalPanOffset = panOffset;
                        
                        // 改变鼠标样式
                        canvas.style.cursor = 'grabbing';
                        e.preventDefault();
                    }
                }
            });

            // 鼠标移动事件
            canvas.addEventListener('mousemove', (e) => {
                const rect = canvas.getBoundingClientRect();
                const x = e.clientX - rect.left;
                const y = e.clientY - rect.top;
                
                if (isDragging) {
                    if (dragType === 'pan') {
                        // 平移拖拽
                        const deltaX = x - dragStartX;
                        const visibleDuration = videoMs / zoomLevel;
                        const deltaTime = (deltaX / (canvas.width - 16)) * visibleDuration; // 16 = 2*pad
                        const newPanOffset = originalPanOffset - deltaTime;
                        
                        // 限制平移范围
                        const maxPanOffset = Math.max(0, videoMs - videoMs / zoomLevel);
                        panOffset = Math.max(0, Math.min(maxPanOffset, newPanOffset));
                        
                        showDragHint(e.clientX, e.clientY, `平移: ${fmtMs(panOffset)} - ${fmtMs(panOffset + visibleDuration)}`);
                        drawTimeline(videoEl.currentTime * 1000);
                    } else if (dragCueIndex >= 0) {
                        // 字幕拖拽
                        const currentTime = screenToTime(x);
                        let newStart = originalStartTime;
                        let newEnd = originalEndTime;
                        
                        if (dragType === 'start') {
                            newStart = Math.max(0, Math.min(originalEndTime - 100, currentTime));
                            showDragHint(e.clientX, e.clientY, `开始时间: ${fmtMs(newStart)}`);
                        } else if (dragType === 'end') {
                            newEnd = Math.min(videoMs, Math.max(originalStartTime + 100, currentTime));
                            showDragHint(e.clientX, e.clientY, `结束时间: ${fmtMs(newEnd)}`);
                        } else if (dragType === 'move') {
                            const duration = originalEndTime - originalStartTime;
                            const deltaTime = currentTime - screenToTime(dragStartX);
                            newStart = Math.max(0, Math.min(videoMs - duration, originalStartTime + deltaTime));
                            newEnd = newStart + duration;
                            showDragHint(e.clientX, e.clientY, `移动: ${fmtMs(newStart)} - ${fmtMs(newEnd)}`);
                        }
                        
                        updateCueTime(dragCueIndex, newStart, newEnd);
                    }
                } else {
                    // 更新鼠标样式
                    const hit = getCueAtPosition(x, y);
                    if (hit) {
                        canvas.style.cursor = 'ew-resize';
                        // 显示悬停提示
                        const cue = cues[hit.index];
                        let hintText = '';
                        if (hit.type === 'start') {
                            hintText = `拖拽调整开始时间: ${fmtMs(cue.start)}`;
                        } else if (hit.type === 'end') {
                            hintText = `拖拽调整结束时间: ${fmtMs(cue.end)}`;
                        } else if (hit.type === 'move') {
                            hintText = `拖拽移动字幕块: ${fmtMs(cue.start)} - ${fmtMs(cue.end)}`;
                        }
                        showDragHint(e.clientX, e.clientY, hintText);
                    } else {
                        canvas.style.cursor = 'grab';
                        hideDragHint();
                    }
                }
            });

            // 鼠标释放事件
            canvas.addEventListener('mouseup', (e) => {
                if (isDragging) {
                    // 如果正在拖拽字幕，立即触发保存
                    if (dragCueIndex >= 0) {
                        triggerAutoSave(true); // 立即保存
                    }
                    
                    isDragging = false;
                    dragType = null;
                    dragCueIndex = -1;
                    canvas.style.cursor = 'grab';
                    hideDragHint();
                }
            });

            // 鼠标离开事件
            canvas.addEventListener('mouseleave', (e) => {
                if (isDragging) {
                    // 如果正在拖拽字幕，立即触发保存
                    if (dragCueIndex >= 0) {
                        triggerAutoSave(true); // 立即保存
                    }
                    
                    isDragging = false;
                    dragType = null;
                    dragCueIndex = -1;
                    canvas.style.cursor = 'grab';
                }
                hideDragHint();
            });

            // 滚轮缩放事件
            canvas.addEventListener('wheel', (e) => {
                e.preventDefault();
                
                const rect = canvas.getBoundingClientRect();
                const x = e.clientX - rect.left;
                const mouseTime = screenToTime(x);
                
                const zoomFactor = e.deltaY > 0 ? 0.8 : 1.25;
                const newZoomLevel = Math.max(0.1, Math.min(10, zoomLevel * zoomFactor));
                
                // 以鼠标位置为中心进行缩放
                const zoomRatio = newZoomLevel / zoomLevel;
                const newPanOffset = mouseTime - (mouseTime - panOffset) * zoomRatio;
                
                zoomLevel = newZoomLevel;
                panOffset = Math.max(0, Math.min(videoMs - videoMs / zoomLevel, newPanOffset));
                
                drawTimeline(videoEl.currentTime * 1000);
            });

            // 键盘快捷键
            document.addEventListener('keydown', (e) => {
                if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
                
                switch(e.key) {
                    case 'r':
                    case 'R':
                        // 重置缩放和平移
                        zoomLevel = 1;
                        panOffset = 0;
                        drawTimeline(videoEl.currentTime * 1000);
                        break;
                    case 'f':
                    case 'F':
                        // 适应窗口大小
                        zoomLevel = 1;
                        panOffset = 0;
                        drawTimeline(videoEl.currentTime * 1000);
                        break;
                    case 'ArrowLeft':
                        // 向左平移
                        const leftStep = videoMs / zoomLevel / 10;
                        panOffset = Math.max(0, panOffset - leftStep);
                        drawTimeline(videoEl.currentTime * 1000);
                        break;
                    case 'ArrowRight':
                        // 向右平移
                        const rightStep = videoMs / zoomLevel / 10;
                        panOffset = Math.min(videoMs - videoMs / zoomLevel, panOffset + rightStep);
                        drawTimeline(videoEl.currentTime * 1000);
                        break;
                }
            });

            // 缩放控制按钮事件
            zoomInBtn.addEventListener('click', () => {
                const newZoomLevel = Math.min(10, zoomLevel * 1.25);
                zoomLevel = newZoomLevel;
                drawTimeline(videoEl.currentTime * 1000);
            });

            zoomOutBtn.addEventListener('click', () => {
                const newZoomLevel = Math.max(0.1, zoomLevel * 0.8);
                zoomLevel = newZoomLevel;
                drawTimeline(videoEl.currentTime * 1000);
            });

            zoomResetBtn.addEventListener('click', () => {
                zoomLevel = 1;
                panOffset = 0;
                drawTimeline(videoEl.currentTime * 1000);
            });

            // 保存按钮点击事件
            saveTimelineBtn.addEventListener('click', async () => {
                // 禁用按钮防止重复点击
                saveTimelineBtn.disabled = true;
                saveTimelineBtn.textContent = '保存中...';
                saveTimelineBtn.style.background = '#6c757d';
                
                try {
                    const success = await saveSubtitles(true);
                    if (success) {
                        // 保存成功，短暂显示成功状态
                        saveTimelineBtn.textContent = '已保存';
                        saveTimelineBtn.style.background = '#28a745';
                        setTimeout(() => {
                            saveTimelineBtn.textContent = '保存';
                            saveTimelineBtn.style.background = '#28a745';
                            saveTimelineBtn.disabled = false;
                        }, 2000);
                    } else {
                        // 保存失败
                        saveTimelineBtn.textContent = '保存失败';
                        saveTimelineBtn.style.background = '#dc3545';
                        setTimeout(() => {
                            saveTimelineBtn.textContent = '保存';
                            saveTimelineBtn.style.background = '#28a745';
                            saveTimelineBtn.disabled = false;
                        }, 3000);
                    }
                } catch (e) {
                    console.error('保存失败:', e);
                    saveTimelineBtn.textContent = '保存失败';
                    saveTimelineBtn.style.background = '#dc3545';
                    setTimeout(() => {
                        saveTimelineBtn.textContent = '保存';
                        saveTimelineBtn.style.background = '#28a745';
                        saveTimelineBtn.disabled = false;
                    }, 3000);
                }
            });

            window.addEventListener('resize', () => drawTimeline(videoEl.currentTime*1000));

            videoEl.addEventListener('timeupdate', () => updateActive(Math.floor(videoEl.currentTime*1000)));

            fetch(`/viewer_api/${taskId}/subtitles`).then(r=>r.json()).then(data => {
                if (data && data.code === 0) {
                    cues = data.subtitles || [];
                    videoMs = data.video_ms || (cues.length? cues[cues.length-1].end : 0);
                    speakers = (data.speakers || []).filter(Boolean);
                    const translationFiles = data.translation_files || [];
                    
                    renderList();
                    drawTimeline(0);
                    
                    // 如果有翻译文件，显示提示
                    if (translationFiles.length > 0) {
                        console.log(`检测到翻译文件: ${translationFiles.join(', ')}`);
                        // 在页面上显示一个通知
                        const notification = document.createElement('div');
                        notification.style.cssText = `
                            position: fixed; top: 20px; right: 20px; z-index: 1000;
                            background: #28a745; color: white; padding: 10px 15px;
                            border-radius: 5px; font-size: 14px; box-shadow: 0 2px 10px rgba(0,0,0,0.2);
                        `;
                        notification.innerHTML = `✅ 已加载翻译文件: ${translationFiles.join(', ')}`;
                        document.body.appendChild(notification);
                        
                        // 3秒后自动隐藏
                        setTimeout(() => {
                            if (notification.parentNode) {
                                notification.parentNode.removeChild(notification);
                            }
                        }, 3000);
                    }
                }
            });

            async function onSaveSrt() {
                try {
                    updateSaveStatus('保存中...', '#ffc107');
                    const payload = { subtitles: cues.map(c => ({
                        start: Number(c.start)||0,
                        end: Number(c.end)||0,
                        text: String(c.text||'').trim(),
                        speaker: String(c.speaker||'').trim(),
                    })), srt_with_spk: true };
                    const res = await fetch(`/viewer_api/${taskId}/export_srt`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });
                    const data = await res.json();
                    if (data && data.code === 0 && data.download_url) {
                        updateSaveStatus('已保存', '#28a745');
                        window.location.href = data.download_url;
                    } else {
                        updateSaveStatus('保存失败', '#dc3545');
                        alert(data && data.msg ? data.msg : '保存失败');
                    }
                } catch (e) {
                    console.error(e);
                    updateSaveStatus('保存失败', '#dc3545');
                    alert('保存失败');
                }
            }

            async function onSaveJson() {
                try {
                    const payload = { subtitles: cues.map(c => ({
                        start: Number(c.start)||0,
                        end: Number(c.end)||0,
                        startraw: c.startraw,
                        endraw: c.endraw,
                        text: String(c.text||'').trim(),
                        speaker: String(c.speaker||'').trim(),
                    })) };
                    const res = await fetch(`/viewer_api/${taskId}/export_json`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });
                    const data = await res.json();
                    if (data && data.code === 0 && data.download_url) {
                        window.location.href = data.download_url;
                    } else {
                        alert(data && data.msg ? data.msg : '保存失败');
                    }
                } catch (e) {
                    console.error(e);
                    alert('保存失败');
                }
            }

            function onAddSubtitles() {
                if (!cues || cues.length === 0) {
                    alert('没有字幕数据，无法添加字幕');
                    return;
                }
                showAddSubModal();
            }

            async function onGenerateTTS() {
                try {
                    if (!cues || cues.length === 0) {
                        alert('没有字幕数据，无法生成TTS音频');
                        return;
                    }
                    
                    const confirmed = confirm('开始生成TTS音频？这将根据字幕内容生成人声音频文件。');
                    if (!confirmed) return;
                    
                    // 显示进度提示
                    btnGenerateTTS.textContent = '生成中...';
                    btnGenerateTTS.disabled = true;
                    
                    // 准备TTS请求数据
                    const payload = { 
                        subtitles: cues.map((c, index) => ({
                            line: index + 1,
                            start_time: Number(c.start) || 0,
                            end_time: Number(c.end) || 0,
                            startraw: c.startraw || '',
                            endraw: c.endraw || '',
                            time: `${c.startraw || ''} --> ${c.endraw || ''}`,
                            text: String(c.text || '').trim(),
                            speaker: String(c.speaker || '').trim(),
                        }))
                    };
                    
                    console.log('发送TTS生成请求数据:', payload);
                    
                    const res = await fetch(`/viewer_api/${taskId}/generate_tts`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });
                    const data = await res.json();
                    
                    if (data && data.code === 0) {
                        alert('TTS音频生成任务已启动，请稍后查看结果');
                        // 可以跳转到任务状态页面
                        window.open(`/tts_result/${data.task_id}`, '_blank');
                    } else {
                        alert(data && data.msg ? data.msg : 'TTS生成启动失败');
                    }
                } catch (e) {
                    console.error(e);
                    alert('TTS生成启动失败');
                } finally {
                    // 恢复按钮状态
                    btnGenerateTTS.textContent = '生成TTS音频';
                    btnGenerateTTS.disabled = false;
                }
            }

            async function onSynthesizeVideo() {
                try {
                    if (!cues || cues.length === 0) {
                        alert('没有字幕数据，无法合成视频');
                        return;
                    }
                    
                    const confirmed = confirm('开始合成视频？这将使用Demucs分离原视频人声，然后与TTS音频合成新视频。');
                    if (!confirmed) return;
                    
                    // 显示进度提示 + 弹窗
                    btnSynthesizeVideo.textContent = '合成中...';
                    btnSynthesizeVideo.disabled = true;
                    showSynthModal('正在启动任务...');
                    
                    // 准备视频合成请求数据
                    const payload = { 
                        subtitles: cues.map((c, index) => ({
                            line: index + 1,
                            start_time: Number(c.start) || 0,
                            end_time: Number(c.end) || 0,
                            startraw: c.startraw || '',
                            endraw: c.endraw || '',
                            time: `${c.startraw || ''} --> ${c.endraw || ''}`,
                            text: String(c.text || '').trim(),
                            speaker: String(c.speaker || '').trim(),
                        }))
                    };
                    
                    console.log('发送视频合成请求数据:', payload);
                    
                    const res = await fetch(`/viewer_api/${taskId}/synthesize_video`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });
                    const data = await res.json();
                    if (data && data.code === 0 && data.task_id) {
                        const synthTaskId = data.task_id;
                        // 轮询任务状态，完成后替换播放器视频源
                        const poll = async () => {
                            try {
                                const r = await fetch(`/task_status?task_id=${synthTaskId}`);
                                const s = await r.json();
                                if (s.code === 0 && s.data && Array.isArray(s.data.url)) {
                                    // 查找 result.mp4
                                    let mp4 = s.data.url.find(u => /\/result\.mp4$/i.test(u));
                                    if (!mp4) {
                                        // 回退任意 mp4
                                        mp4 = s.data.url.find(u => /\.mp4$/i.test(u));
                                    }
                                    if (mp4) {
                                        hideSynthModal();
                                        videoEl.src = mp4;
                                        try { videoEl.load(); videoEl.play(); } catch (e) {}
                                        return true;
                                    } else {
                                        hideSynthModal();
                                        // 打开结果页作为回退
                                        window.open(`/synthesis_result/${synthTaskId}`, '_blank');
                                        return true;
                                    }
                                } else if (s.code === -1) {
                                    // 处理中
                                    synthModalMsg.textContent = s.msg || '正在处理，请稍候...';
                                    return false;
                                } else {
                                    synthModalMsg.textContent = (s && s.msg) ? `失败：${s.msg}` : '任务失败';
                                    return true;
                                }
                            } catch (e) {
                                synthModalMsg.textContent = '状态检查失败，稍后重试...';
                                return false;
                            }
                        };
                        // 启动轮询
                        let done = false;
                        showSynthModal('任务已启动，正在处理中...');
                        for (let i = 0; i < 360; i++) { // 最长约12分钟（2s * 360）
                            // eslint-disable-next-line no-await-in-loop
                            done = await poll();
                            if (done) break;
                            // eslint-disable-next-line no-await-in-loop
                            await new Promise(r => setTimeout(r, 2000));
                        }
                        if (!done) {
                            hideSynthModal();
                            alert('合成超时，请稍后在结果页查看');
                            window.open(`/synthesis_result/${synthTaskId}`, '_blank');
                        }
                    } else {
                        alert(data && data.msg ? data.msg : '视频合成启动失败');
                    }
                } catch (e) {
                    console.error(e);
                    alert('视频合成启动失败');
                } finally {
                    // 恢复按钮状态
                    btnSynthesizeVideo.textContent = '合成视频';
                    btnSynthesizeVideo.disabled = false;
                    hideSynthModal();
                }
            }

            async function onTranslateSubtitle() {
                try {
                    if (!cues || cues.length === 0) {
                        alert('没有字幕数据，无法进行翻译');
                        return;
                    }
                    
                    // 创建语言选择对话框
                    const languageOptions = [
                        { code: 'en', name: '英语' },
                        { code: 'fr', name: '法语' },
                        { code: 'ja', name: '日语' },
                        { code: 'zh-cn', name: '汉语' },
                        { code: 'es', name: '西班牙语' },
                        { code: 'pt', name: '葡萄牙语' },
                        { code: 'th', name: '泰语' }
                    ];
                    
                    // 创建对话框HTML
                    const dialogHtml = `
                        <div id="translateDialog" style="
                            position: fixed;
                            top: 0;
                            left: 0;
                            width: 100%;
                            height: 100%;
                            background: rgba(0,0,0,0.5);
                            display: flex;
                            justify-content: center;
                            align-items: center;
                            z-index: 1000;
                        ">
                            <div style="
                                background: white;
                                padding: 24px;
                                border-radius: 8px;
                                box-shadow: 0 4px 20px rgba(0,0,0,0.3);
                                min-width: 300px;
                            ">
                                <h3 style="margin: 0 0 16px 0;">选择翻译语言</h3>
                                <select id="targetLanguage" style="
                                    width: 100%;
                                    padding: 8px;
                                    border: 1px solid #ddd;
                                    border-radius: 4px;
                                    margin-bottom: 16px;
                                ">
                                    ${languageOptions.map(opt => `<option value="${opt.code}">${opt.name}</option>`).join('')}
                                </select>
                                <div style="display: flex; gap: 8px; justify-content: flex-end;">
                                    <button id="cancelTranslate" style="
                                        padding: 8px 16px;
                                        border: 1px solid #ddd;
                                        background: white;
                                        border-radius: 4px;
                                        cursor: pointer;
                                    ">取消</button>
                                    <button id="confirmTranslate" style="
                                        padding: 8px 16px;
                                        border: none;
                                        background: #9c27b0;
                                        color: white;
                                        border-radius: 4px;
                                        cursor: pointer;
                                    ">开始翻译</button>
                                </div>
                            </div>
                        </div>
                    `;
                    
                    // 添加对话框到页面
                    document.body.insertAdjacentHTML('beforeend', dialogHtml);
                    
                    const dialog = document.getElementById('translateDialog');
                    const cancelBtn = document.getElementById('cancelTranslate');
                    const confirmBtn = document.getElementById('confirmTranslate');
                    const targetLanguageSelect = document.getElementById('targetLanguage');
                    
                    // 取消按钮事件
                    cancelBtn.addEventListener('click', () => {
                        document.body.removeChild(dialog);
                    });
                    
                    // 确认按钮事件
                    confirmBtn.addEventListener('click', async () => {
                        const targetLanguage = targetLanguageSelect.value;
                        document.body.removeChild(dialog);
                        
                        // 显示进度提示
                        btnTranslateSubtitle.textContent = '翻译中...';
                        btnTranslateSubtitle.disabled = true;
                        
                        try {
                            // 准备翻译请求数据
                            const payload = {
                                subtitles: cues.map((c, index) => ({
                                    line: index + 1,
                                    start_time: Number(c.start) || 0,
                                    end_time: Number(c.end) || 0,
                                    startraw: c.startraw || '',
                                    endraw: c.endraw || '',
                                    time: `${c.startraw || ''} --> ${c.endraw || ''}`,
                                    text: String(c.text || '').trim(),
                                    speaker: String(c.speaker || '').trim(),
                                })),
                                target_language: targetLanguage,
                                translate_type: 0  // 使用Google翻译
                            };
                            
                            console.log('发送翻译请求数据:', payload);
                            
                            const res = await fetch(`/viewer_api/${taskId}/translate_subtitles`, {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify(payload)
                            });
                            const data = await res.json();
                            
                            if (data && data.code === 0) {
                                // 将翻译结果添加到字幕数据中
                                if (data.translated_subtitles && data.translated_subtitles.length > 0) {
                                    data.translated_subtitles.forEach((translated, index) => {
                                        if (cues[index]) {
                                            cues[index].translated_text = translated.text;
                                        }
                                    });
                                    // 重新渲染列表
                                    renderList();
                                    // 检查是否有语音克隆映射，如果有则显示生成音频按钮
                                    checkVoiceMapping().then(hasMapping => {
                                        if (hasMapping) {
                                            btnGenerateAudio.style.display = 'inline-block';
                                        }
                                    });
                                    
                                    // 检查是否有已生成的音频文件，如果有则显示合成音频按钮
                                    checkGeneratedAudio().then(hasAudio => {
                                        if (hasAudio) {
                                            btnSynthesizeAudio.style.display = 'inline-block';
                                        }
                                    });
                                    alert(`翻译完成！已生成翻译文件：${data.srt_file}。您可以编辑翻译结果，修改会自动保存。`);
                                } else {
                                    alert('翻译失败：没有返回翻译结果');
                                }
                            } else {
                                alert(data && data.msg ? data.msg : '翻译失败');
                            }
                        } catch (e) {
                            console.error(e);
                            alert('翻译失败');
                        } finally {
                            // 恢复按钮状态
                            btnTranslateSubtitle.textContent = '翻译字幕';
                            btnTranslateSubtitle.disabled = false;
                        }
                    });
                    
                } catch (e) {
                    console.error(e);
                    alert('翻译启动失败');
                }
            }

            // 保存翻译结果功能
            async function onSaveTranslation() {
                try {
                    if (!cues || cues.length === 0) {
                        alert('没有字幕数据');
                        return;
                    }
                    
                    // 检查是否有翻译内容
                    const hasTranslation = cues.some(c => c.translated_text && c.translated_text.trim());
                    if (!hasTranslation) {
                        alert('没有翻译内容需要保存');
                        return;
                    }
                    
                    btnSaveTranslation.textContent = '保存中...';
                    btnSaveTranslation.disabled = true;
                    
                    // 准备保存数据
                    const saveData = {
                        subtitles: cues.map((c, index) => ({
                            line: index + 1,
                            start_time: Number(c.start) || 0,
                            end_time: Number(c.end) || 0,
                            startraw: c.startraw || '',
                            endraw: c.endraw || '',
                            time: `${c.startraw || ''} --> ${c.endraw || ''}`,
                            text: String(c.text || '').trim(),
                            translated_text: String(c.translated_text || '').trim(),
                            speaker: String(c.speaker || '').trim(),
                        }))
                    };
                    
                    const res = await fetch(`/viewer_api/${taskId}/save_translation`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(saveData)
                    });
                    const data = await res.json();
                    
                    if (data && data.code === 0) {
                        alert('翻译结果保存成功！');
                        btnSaveTranslation.style.display = 'none';
                    } else {
                        alert(data && data.msg ? data.msg : '保存失败');
                    }
                } catch (e) {
                    console.error(e);
                    alert('保存失败');
                } finally {
                    btnSaveTranslation.textContent = '保存翻译';
                    btnSaveTranslation.disabled = false;
                }
            }

            // 语音克隆功能
            async function onVoiceClone() {
                try {
                    if (!cues || cues.length === 0) {
                        alert('没有字幕数据');
                        return;
                    }
                    
                    // 检查是否有说话人信息
                    const speakers = [...new Set(cues.map(c => c.speaker).filter(s => s && s.trim()))];
                    if (speakers.length === 0) {
                        alert('没有检测到说话人信息，无法进行语音克隆');
                        return;
                    }
                    
                    // 显示说话人信息确认对话框
                    const speakerList = speakers.map(s => `<li>${s}</li>`).join('');
                    const confirmMessage = `检测到以下说话人，将为他们创建语音克隆：\n\n${speakers.join(', ')}\n\n是否继续？`;
                    
                    if (!confirm(confirmMessage)) {
                        return;
                    }
                    
                    btnVoiceClone.textContent = '语音克隆中...';
                    btnVoiceClone.disabled = true;
                    
                    // 准备语音克隆请求数据
                    const payload = {
                        speakers: speakers,
                        subtitles: cues.map((c, index) => ({
                            line: index + 1,
                            start_time: Number(c.start) || 0,
                            end_time: Number(c.end) || 0,
                            startraw: c.startraw || '',
                            endraw: c.endraw || '',
                            time: `${c.startraw || ''} --> ${c.endraw || ''}`,
                            text: String(c.text || '').trim(),
                            speaker: String(c.speaker || '').trim(),
                        }))
                    };
                    
                    console.log('发送语音克隆请求数据:', payload);
                    
                    const res = await fetch(`/viewer_api/${taskId}/voice_clone`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });
                    const data = await res.json();
                    
                    if (data && data.code === 0) {
                        if (data.voice_clones && data.voice_clones.length > 0) {
                            alert(`语音克隆完成！成功为 ${data.voice_clones.length} 个说话人创建了语音克隆。`);
                            console.log('语音克隆结果:', data.voice_clones);
                            // 显示生成音频按钮
                            btnGenerateAudio.style.display = 'inline-block';
                        } else {
                            alert('语音克隆失败：没有返回克隆结果');
                        }
                    } else {
                        alert(data && data.msg ? data.msg : '语音克隆失败');
                    }
                } catch (e) {
                    console.error(e);
                    alert('语音克隆失败');
                } finally {
                    btnVoiceClone.textContent = '语音克隆';
                    btnVoiceClone.disabled = false;
                }
            }

            // 合成音频功能 - 直接合成已生成的音频文件
            async function onSynthesizeAudio() {
                try {
                    if (!cues || cues.length === 0) {
                        alert('没有字幕数据');
                        return;
                    }
                    
                    // 检查是否有翻译字幕
                    const hasTranslation = cues.some(cue => (cue.translated_text && cue.translated_text.trim()) || (cue.translation && cue.translation.trim()));
                    if (!hasTranslation) {
                        alert('请先翻译字幕');
                        return;
                    }
                    
                    // 确认合成音频
                    const confirmMessage = `即将合成 ${cues.length} 条字幕的音频文件。\n\n是否继续？`;
                    if (!confirm(confirmMessage)) {
                        return;
                    }
                    
                    btnSynthesizeAudio.textContent = '合成音频中...';
                    btnSynthesizeAudio.disabled = true;
                    
                    console.log('开始合成音频...');
                    
                    const res = await fetch(`/viewer_api/${taskId}/synthesize_audio`, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({
                            subtitles: cues.map((c, index) => ({
                                line: index + 1,
                                start_time: Number(c.start) || 0,
                                end_time: Number(c.end) || 0,
                                text: c.translated_text || c.translation || c.text,
                                speaker: c.speaker || 'speaker_0'
                            }))
                        })
                    });
                    
                    const data = await res.json();
                    if (data.code === 0) {
                        alert(`音频合成成功！\\n输出文件: ${data.output_file}`);
                        console.log('合成结果:', data);
                    } else {
                        alert(`音频合成失败: ${data.msg}`);
                    }
                } catch (e) {
                    console.error(e);
                    alert('音频合成失败');
                } finally {
                    btnSynthesizeAudio.textContent = '合成音频';
                    btnSynthesizeAudio.disabled = false;
                }
            }

            // 生成音频功能
            async function onGenerateAudio() {
                try {
                    if (!cues || cues.length === 0) {
                        alert('没有字幕数据');
                        return;
                    }
                    
                    // 检查是否有翻译内容
                    const hasTranslation = cues.some(c => c.translated_text && c.translated_text.trim());
                    if (!hasTranslation) {
                        alert('没有翻译内容，请先进行字幕翻译');
                        return;
                    }
                    
                    // 检查是否有语音克隆映射
                    const hasVoiceMapping = await checkVoiceMapping();
                    if (!hasVoiceMapping) {
                        alert('没有找到语音克隆映射，请先进行语音克隆');
                        return;
                    }
                    
                    // 确认生成音频
                    const confirmMessage = `即将为 ${cues.length} 条翻译字幕生成音频，使用语音克隆技术。\n\n是否继续？`;
                    if (!confirm(confirmMessage)) {
                        return;
                    }
                    
                    btnGenerateAudio.textContent = '生成音频中...';
                    btnGenerateAudio.disabled = true;
                    
                    // 准备生成音频请求数据
                    const payload = {
                        subtitles: cues.map((c, index) => ({
                            line: index + 1,
                            start_time: Number(c.start) || 0,
                            end_time: Number(c.end) || 0,
                            startraw: c.startraw || '',
                            endraw: c.endraw || '',
                            time: `${c.startraw || ''} --> ${c.endraw || ''}`,
                            text: String(c.text || '').trim(),
                            translated_text: String(c.translated_text || '').trim(),
                            speaker: String(c.speaker || '').trim(),
                        }))
                    };
                    
                    console.log('发送生成音频请求数据:', payload);
                    
                    const res = await fetch(`/viewer_api/${taskId}/generate_audio`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });
                    const data = await res.json();
                    
                    if (data && data.code === 0) {
                        alert(`音频生成完成！\n\n生成的文件：\n${data.audio_file}\n\n总时长：${data.duration || '未知'}`);
                        console.log('音频生成结果:', data);
                    } else {
                        alert(data && data.msg ? data.msg : '音频生成失败');
                    }
                } catch (e) {
                    console.error(e);
                    alert('音频生成失败');
                } finally {
                    btnGenerateAudio.textContent = '生成音频';
                    btnGenerateAudio.disabled = false;
                }
            }

            // 检查语音克隆映射是否存在
            async function checkVoiceMapping() {
                try {
                    const res = await fetch(`/viewer_api/${taskId}/check_voice_mapping`);
                    const data = await res.json();
                    return data && data.code === 0 && data.has_mapping;
                } catch (e) {
                    console.error('检查语音映射失败:', e);
                    return false;
                }
            }
            
            // 检查是否有已生成的音频文件
            async function checkGeneratedAudio() {
                try {
                    const res = await fetch(`/viewer_api/${taskId}/check_generated_audio`);
                    const data = await res.json();
                    return data && data.code === 0 && data.has_audio;
                } catch (e) {
                    console.error('检查已生成音频失败:', e);
                    return false;
                }
            }

            btnTranslateSubtitle.addEventListener('click', onTranslateSubtitle);
            btnSaveTranslation.addEventListener('click', onSaveTranslation);
            btnVoiceClone.addEventListener('click', onVoiceClone);
            btnGenerateAudio.addEventListener('click', onGenerateAudio);
            btnSynthesizeAudio.addEventListener('click', onSynthesizeAudio);
            btnSynthesizeVideo.addEventListener('click', onSynthesizeVideo);
            btnAddSubtitles.addEventListener('click', onAddSubtitles);
            btnSaveSrt.addEventListener('click', onSaveSrt);
            btnSaveJson.addEventListener('click', onSaveJson);
            </script>
        </body>
        </html>
        """
        html = html.replace('((VIDEO_URL))', video_url)
        html = html.replace('((TASK_ID))', task_id)
        html = html.replace('((TASK_ID_JSON))', json.dumps(task_id))
        # 将模板中为规避 Python/Jinja 冲突而使用的双花括号恢复为单花括号
        html = html.replace('{{', '{').replace('}}', '}')
        return html

    @app.route('/viewer_api/<task_id>/subtitles', methods=['GET'])
    def viewer_subtitles(task_id):
        # 返回解析后的字幕 JSON，以及视频总时长（毫秒）
        task_dir = Path(TARGET_DIR) / task_id
        if not task_dir.exists():
            return jsonify({"code": 1, "msg": "任务不存在"}), 404

        # 挑选文件
        from videotrans.util import help_srt, help_ffmpeg
        files = [f for f in task_dir.iterdir() if f.is_file()]
        srt_path = None
        video_path = None
        from videotrans.configure import config as _cfg
        exts = set([e.lower() for e in _cfg.VIDEO_EXTS + _cfg.AUDIO_EXITS])
        
        # 优先选择raw.srt文件
        srt_files = [f for f in files if f.name.lower().endswith('.srt')]
        print(f"找到的SRT文件: {[f.name for f in srt_files]}")
        
        # 首先查找raw.srt文件
        raw_srt_path = task_dir / "raw.srt"
        if raw_srt_path.exists():
            srt_path = raw_srt_path
            print(f"选择SRT文件: raw.srt")
        else:
            # 如果没有raw.srt，选择第一个SRT文件
            if srt_files:
                srt_path = srt_files[0]
                print(f"选择第一个SRT文件: {srt_path.name}")
            else:
                srt_path = None
            
        for f in files:
            lower = f.name.lower()
            if any(lower.endswith('.' + e) for e in exts):
                if video_path is None:
                    video_path = f
        if not srt_path or not video_path:
            return jsonify({"code": 1, "msg": "任务文件缺失（需要视频与srt）"}), 400

        # 解析字幕（保持原有逻辑不变）
        print(f"开始解析SRT文件: {srt_path}")
        subs = help_srt.get_subtitle_from_srt(srt_path.as_posix())
        print(f"解析到 {len(subs)} 条字幕")
        
        # 提取说话人并清理文本（按首行 [xxx] 解析）
        import re as _re
        parsed = []
        spk_set = set()
        for it in subs:
            text = it.get('text', '') or ''
            speaker = ''
            if text:
                first_line, *rest = text.split('\n')
                m = _re.match(r'^\s*\[([^\]]+)\]\s*(.*)$', first_line)
                if m:
                    speaker = m.group(1).strip()
                    first_line = m.group(2).strip()
                text = '\n'.join([first_line] + rest).strip()
            if speaker:
                spk_set.add(speaker)
            parsed.append({
                'line': int(it.get('line', len(parsed)+1)),
                'start': int(it.get('start_time')) if 'start_time' in it else 0,
                'end': int(it.get('end_time')) if 'end_time' in it else 0,
                'startraw': it.get('startraw') or it.get('time', '').split(' --> ')[0] if it.get('time') else help_srt.ms_to_time_string(ms=int(it.get('start_time',0))),
                'endraw': it.get('endraw') or it.get('time', '').split(' --> ')[-1] if it.get('time') else help_srt.ms_to_time_string(ms=int(it.get('end_time',0))),
                'text': text,
                'speaker': speaker,
                'duration': (int(it.get('end_time', 0)) - int(it.get('start_time', 0))) if ('end_time' in it and 'start_time' in it) else 0,
            })

        # 检测并加载翻译文件（不影响原有逻辑）
        translation_files = {}
        for f in files:
            if f.name.startswith('translated_') and f.name.endswith('.srt'):
                # 提取语言代码
                lang_code = f.name.replace('translated_', '').replace('.srt', '')
                try:
                    # 解析翻译文件
                    translated_srt = help_srt.get_subtitle_from_srt(f.as_posix())
                    translation_files[lang_code] = translated_srt
                    print(f"检测到翻译文件: {f.name}, 语言: {lang_code}")
                except Exception as e:
                    print(f"解析翻译文件 {f.name} 失败: {e}")

        # 将翻译内容填充到字幕项中
        for subtitle_item in parsed:
            for lang_code, translated_srt in translation_files.items():
                # 查找对应的翻译文本（通过时间匹配）
                for trans_item in translated_srt:
                    if (abs(trans_item.get('start_time', 0) - subtitle_item.get('start', 0)) < 100 and
                        abs(trans_item.get('end_time', 0) - subtitle_item.get('end', 0)) < 100):
                        subtitle_item[f'translated_text_{lang_code}'] = trans_item.get('text', '')
                        break

        # 视频总时长（毫秒）
        try:
            video_ms = int(help_ffmpeg.get_video_duration(video_path.as_posix()) or 0)
        except Exception:
            video_ms = parsed[-1]['end'] if parsed else 0

        print(f"解析完成，共 {len(parsed)} 条字幕")
        print(f"说话人: {sorted(list(spk_set))}")
        print(f"翻译文件: {list(translation_files.keys())}")
        
        return jsonify({
            "code": 0, 
            "msg": "ok", 
            "subtitles": parsed, 
            "video_ms": video_ms, 
            "speakers": sorted(list(spk_set)),
            "translation_files": list(translation_files.keys())
        })

    @app.route('/viewer_api/<task_id>/export_srt', methods=['POST'])
    def viewer_export_srt(task_id):
        # 将前端编辑后的字幕导出为 SRT 文件，并返回下载链接
        task_dir = Path(TARGET_DIR) / task_id
        if not task_dir.exists():
            return jsonify({"code": 1, "msg": "任务不存在"}), 404

        data = request.get_json(silent=True) or {}
        items = data.get('subtitles', [])
        if not isinstance(items, list) or len(items) < 1:
            return jsonify({"code": 1, "msg": "无有效字幕"}), 400

        # 组装为 help_srt.get_srt_from_list 接受的结构
        from videotrans.util import help_srt
        srt_list = []
        for i, it in enumerate(items, start=1):
            start = int(it.get('start', 0))
            end = int(it.get('end', 0))
            speaker = (it.get('speaker') or '').strip()
            text = (it.get('text') or '').strip()
            # 根据入参决定是否在文本首行恢复 [spk] 前缀
            if data.get('srt_with_spk') and speaker:
                if '\n' in text:
                    first, *rest = text.split('\n')
                    text = f"[{speaker}] " + first.strip()
                    if rest:
                        text += "\n" + "\n".join(rest)
                else:
                    text = f"[{speaker}] " + text
            srt_list.append({
                'line': i,
                'start_time': start,
                'end_time': end,
                'text': text,
            })

        try:
            srt_str = help_srt.get_srt_from_list(srt_list)
        except Exception as e:
            return jsonify({"code": 2, "msg": f"生成SRT失败: {str(e)}"}), 500

        # 保存到raw.srt文件
        raw_srt_path = task_dir / "raw.srt"
        raw_srt_path.write_text(srt_str, encoding='utf-8')
        download_url = f'/{API_RESOURCE}/{task_id}/raw.srt'
        print(f"字幕已保存到: raw.srt")

        return jsonify({"code": 0, "msg": "ok", "download_url": download_url})

    @app.route('/viewer_api/<task_id>/export_json', methods=['POST'])
    def viewer_export_json(task_id):
        # 导出 JSON，包含时间范围、spk 与文字内容
        task_dir = Path(TARGET_DIR) / task_id
        if not task_dir.exists():
            return jsonify({"code": 1, "msg": "任务不存在"}), 404

        data = request.get_json(silent=True) or {}
        items = data.get('subtitles', [])
        if not isinstance(items, list) or len(items) < 1:
            return jsonify({"code": 1, "msg": "无有效字幕"}), 400

        # 规范化字段
        out_items = []
        for it in items:
            out_items.append({
                'start': int(it.get('start', 0)),
                'end': int(it.get('end', 0)),
                'startraw': it.get('startraw') or '',
                'endraw': it.get('endraw') or '',
                'speaker': (it.get('speaker') or '').strip(),
                'text': (it.get('text') or '').strip(),
            })

        out_name = f'edited_{int(time.time())}.json'
        out_path = (task_dir / out_name).as_posix()
        Path(out_path).write_text(json.dumps({'subtitles': out_items}, ensure_ascii=False, indent=2), encoding='utf-8')
        download_url = f'/{API_RESOURCE}/{task_id}/{out_name}'
        return jsonify({"code": 0, "msg": "ok", "download_url": download_url})

    @app.route('/viewer_api/<task_id>/export_json_full', methods=['POST'])
    def viewer_export_json_full(task_id):
        # 将前端编辑后的字幕导出为 JSON 文件，并返回下载链接
        task_dir = Path(TARGET_DIR) / task_id
        if not task_dir.exists():
            return jsonify({"code": 1, "msg": "任务不存在"}), 404

        data = request.get_json(silent=True) or {}
        items = data.get('subtitles', [])
        if not isinstance(items, list) or len(items) < 1:
            return jsonify({"code": 1, "msg": "无有效字幕"}), 400

        # 组装为 JSON 格式
        json_data = {
            "subtitles": items,
            "video_ms": data.get('video_ms', 0),
            "speakers": data.get('speakers', [])
        }

        try:
            json_str = json.dumps(json_data, ensure_ascii=False)
        except Exception as e:
            return jsonify({"code": 2, "msg": f"生成JSON失败: {str(e)}"}), 500

        out_name = f'edited_{int(time.time())}.json'
        out_path = (task_dir / out_name).as_posix()
        Path(out_path).write_text(json_str, encoding='utf-8')

        download_url = f'/{API_RESOURCE}/{task_id}/{out_name}'
        return jsonify({"code": 0, "msg": "ok", "download_url": download_url})

    @app.route('/viewer_api/<task_id>/synthesize_video', methods=['POST'])
    def viewer_synthesize_video(task_id):
        """视频合成接口 - 使用 aucs分离人声并与TTS音频合成视频"""
        data = request.json
        if not data or 'subtitles' not in data:
            return jsonify({"code": 1, "msg": "缺少字幕数据"}), 400

        task_dir = Path(TARGET_DIR) / task_id
        if not task_dir.exists():
            return jsonify({"code": 1, "msg": "任务不存在"}), 404

        try:
            # 查找原始视频文件
            files = [f for f in task_dir.iterdir() if f.is_file()]
            video_path = None
            from videotrans.configure import config as _cfg
            exts = set([e.lower() for e in _cfg.VIDEO_EXTS + _cfg.AUDIO_EXITS])
            for f in files:
                lower = f.name.lower()
                if any(lower.endswith(f'.{ext}') for ext in exts):
                    video_path = f
                    break
            
            if not video_path:
                return jsonify({"code": 1, "msg": "未找到视频文件"}), 400

            # 创建新的视频合成任务
            synthesis_task_id = f"synthesis_{task_id}_{int(time.time())}"
            synthesis_dir = Path(TARGET_DIR) / synthesis_task_id
            synthesis_dir.mkdir(parents=True, exist_ok=True)

            # 启动视频合成任务
            threading.Thread(target=start_video_synthesis_task, args=(
                synthesis_task_id, 
                str(video_path),
                data['subtitles']
            )).start()

            return jsonify({
                "code": 0,
                "msg": "视频合成任务已启动",
                "task_id": synthesis_task_id
            })

        except Exception as e:
            return jsonify({"code": 1, "msg": f"启动视频合成失败: {str(e)}"}), 500

    @app.route('/viewer_api/<task_id>/generate_tts', methods=['POST'])
    def viewer_generate_tts(task_id):
        """TTS音频生成接口 - 根据字幕内容生成人声音频"""
        data = request.json
        if not data or 'subtitles' not in data:
            return jsonify({"code": 1, "msg": "缺少字幕数据"}), 400

        task_dir = Path(TARGET_DIR) / task_id
        if not task_dir.exists():
            return jsonify({"code": 1, "msg": "任务不存在"}), 404

        try:
            # 创建新的TTS任务
            tts_task_id = f"tts_{task_id}_{int(time.time())}"
            tts_dir = Path(TARGET_DIR) / tts_task_id
            tts_dir.mkdir(parents=True, exist_ok=True)

            # 启动TTS生成任务
            threading.Thread(target=start_tts_generation_task, args=(
                tts_task_id, 
                data['subtitles']
            )).start()

            return jsonify({
                "code": 0,
                "msg": "TTS生成任务已启动",
                "task_id": tts_task_id
            })

        except Exception as e:
            return jsonify({"code": 1, "msg": f"启动TTS生成失败: {str(e)}"}), 500

    @app.route('/viewer_api/<task_id>/voice_dubbing', methods=['POST'])
    def viewer_voice_dubbing(task_id):
        """智能配音接口 - 根据字幕和说话人信息进行多角色配音"""
        data = request.json
        if not data or 'subtitles' not in data:
            return jsonify({"code": 1, "msg": "缺少字幕数据"}), 400

        task_dir = Path(TARGET_DIR) / task_id
        if not task_dir.exists():
            return jsonify({"code": 1, "msg": "任务不存在"}), 404

        try:
            # 查找原始视频文件
            files = [f for f in task_dir.iterdir() if f.is_file()]
            video_path = None
            from videotrans.configure import config as _cfg
            exts = set([e.lower() for e in _cfg.VIDEO_EXTS + _cfg.AUDIO_EXITS])
            for f in files:
                lower = f.name.lower()
                if any(lower.endswith(f'.{ext}') for ext in exts):
                    video_path = f
                    break
            
            if not video_path:
                return jsonify({"code": 1, "msg": "未找到视频文件"}), 400

            # 创建新的配音任务
            dubbing_task_id = f"dubbing_{task_id}_{int(time.time())}"
            dubbing_dir = Path(TARGET_DIR) / dubbing_task_id
            dubbing_dir.mkdir(parents=True, exist_ok=True)

            # 复制原始视频到配音任务目录
            import shutil
            target_video = dubbing_dir / video_path.name
            shutil.copy2(video_path, target_video)

            # 生成SRT文件 - 前端已提供完整格式的数据
            subtitles = data['subtitles']
            
            # 验证数据格式（前端应该已经提供了完整格式）
            print(f"收到字幕数据: {len(subtitles)} 条")
            if subtitles:
                print(f"第一条字幕示例: {subtitles[0]}")
            
            # 直接使用前端提供的完整格式数据生成SRT
            srt_content = tools.get_srt_from_list(subtitles)
            srt_file = dubbing_dir / f"subtitles_{int(time.time())}.srt"
            
            # 确保SRT文件使用UTF-8编码，并处理可能的编码问题
            try:
                srt_file.write_text(srt_content, encoding='utf-8')
                # 验证文件可以正确读取
                with open(srt_file, 'r', encoding='utf-8') as f:
                    test_content = f.read()
                print(f"SRT文件生成成功，长度: {len(test_content)} 字符")
            except UnicodeEncodeError:
                # 如果UTF-8编码失败，尝试其他编码
                print("UTF-8编码失败，尝试GBK编码")
                srt_file.write_text(srt_content, encoding='gbk')
            except Exception as e:
                print(f"SRT文件生成失败: {str(e)}")
                # 最后尝试，使用错误处理
                srt_file.write_text(srt_content, encoding='utf-8', errors='replace')
                print("已使用错误替换模式生成SRT文件")

            # 启动人声分离和配音任务
            threading.Thread(target=start_voice_dubbing_task, args=(
                dubbing_task_id, 
                str(target_video), 
                str(srt_file),
                subtitles
            )).start()

            return jsonify({
                "code": 0,
                "msg": "配音任务已启动",
                "task_id": dubbing_task_id
            })

        except Exception as e:
            return jsonify({"code": 1, "msg": f"启动配音失败: {str(e)}"}), 500

    @app.route('/viewer_api/<task_id>/translate_subtitles', methods=['POST'])
    def viewer_translate_subtitles(task_id):
        """翻译字幕接口 - 将字幕翻译为指定语言"""
        data = request.json
        if not data or 'subtitles' not in data or 'target_language' not in data:
            return jsonify({"code": 1, "msg": "缺少字幕数据或目标语言"}), 400

        task_dir = Path(TARGET_DIR) / task_id
        if not task_dir.exists():
            return jsonify({"code": 1, "msg": "任务不存在"}), 404

        try:
            from videotrans import translator
            from videotrans.configure import config as _config
            
            # 设置翻译状态，确保翻译器不会提前退出
            original_status = _config.current_status
            original_box_trans = _config.box_trans
            _config.current_status = 'ing'
            _config.box_trans = 'ing'
            
            try:
                # 获取翻译参数
                subtitles = data['subtitles']
                target_language = data['target_language']
                translate_type = data.get('translate_type', 0)  # 默认使用Google翻译
                
                # 准备翻译数据 - 翻译器期望的是包含字典的列表，每个字典有text字段
                text_list = []
                for subtitle in subtitles:
                    text = subtitle.get('text', '').strip()
                    if text:
                        text_list.append({'text': text})
                
                if not text_list:
                    return jsonify({"code": 1, "msg": "没有可翻译的文本内容"}), 400
                
                print(f"开始翻译 {len(text_list)} 条字幕到 {target_language}")
                print(f"翻译数据示例: {text_list[:2] if text_list else 'None'}")
                
                # 调用翻译功能
                translated_texts = translator.run(
                    translate_type=translate_type,
                    text_list=text_list,
                    target_code=target_language,
                    source_code='zh-cn'  # 假设源语言是中文
                )
                
                print(f"翻译结果类型: {type(translated_texts)}")
                print(f"翻译结果长度: {len(translated_texts) if translated_texts else 0}")
                print(f"翻译结果示例: {translated_texts[:2] if translated_texts else 'None'}")
                
                if not translated_texts:
                    return jsonify({"code": 1, "msg": "翻译失败"}), 500
                
                # 翻译器返回的是修改后的text_list，每个元素包含翻译后的text字段
                # 构建返回结果
                translated_subtitles = []
                for i, subtitle in enumerate(subtitles):
                    translated_subtitle = subtitle.copy()
                    # 从翻译结果中获取对应的翻译文本
                    if i < len(translated_texts) and isinstance(translated_texts[i], dict):
                        translated_subtitle['text'] = translated_texts[i].get('text', subtitle['text'])
                    else:
                        translated_subtitle['text'] = subtitle['text']
                    translated_subtitles.append(translated_subtitle)
                
                # 生成带语言后缀的SRT文件
                from videotrans.util import help_srt
                import time
                
                # 创建翻译后的SRT内容（携带原始时间，单位毫秒）
                srt_list = []
                for i, subtitle in enumerate(translated_subtitles):
                    # 前端传入的是 start_time/end_time（毫秒）；保持毫秒不变
                    st = int(subtitle.get('start_time', subtitle.get('start', 0)) or 0)
                    et = int(subtitle.get('end_time', subtitle.get('end', 0)) or 0)
                    srt_list.append({
                        'line': i + 1,
                        'start_time': st,
                        'end_time': et,
                        'text': subtitle.get('text', ''),
                    })
                
                srt_str = help_srt.get_srt_from_list(srt_list)
                
                # 生成带语言后缀的文件名
                language_suffix = target_language.lower()
                srt_filename = f'translated_{language_suffix}.srt'
                srt_path = task_dir / srt_filename
                srt_path.write_text(srt_str, encoding='utf-8')
                
                return jsonify({
                    "code": 0,
                    "msg": "翻译完成",
                    "translated_subtitles": translated_subtitles,
                    "srt_file": srt_filename
                })
                
            finally:
                # 恢复原始状态
                _config.current_status = original_status
                _config.box_trans = original_box_trans
            
        except Exception as e:
            print(f"翻译失败: {str(e)}")
            import traceback
            traceback.print_exc()
            return jsonify({"code": 1, "msg": f"翻译失败: {str(e)}"}), 500

    @app.route('/viewer_api/<task_id>/save_translation', methods=['POST'])
    def viewer_save_translation(task_id):
        """保存翻译结果接口"""
        data = request.json
        if not data or 'subtitles' not in data or 'target_language' not in data:
            return jsonify({"code": 1, "msg": "缺少字幕数据或目标语言"}), 400

        task_dir = Path(TARGET_DIR) / task_id
        if not task_dir.exists():
            return jsonify({"code": 1, "msg": "任务不存在"}), 404

        try:
            from videotrans.util import help_srt
            
            subtitles = data['subtitles']
            target_language = data['target_language']
            
            # 创建翻译后的SRT内容（携带时间，单位毫秒）
            srt_list = []
            for i, subtitle in enumerate(subtitles):
                st = int(subtitle.get('start_time', subtitle.get('start', 0)) or 0)
                et = int(subtitle.get('end_time', subtitle.get('end', 0)) or 0)
                srt_list.append({
                    'line': i + 1,
                    'start_time': st,
                    'end_time': et,
                    'text': subtitle.get('translated_text', ''),
                })
            
            srt_str = help_srt.get_srt_from_list(srt_list)
            
            # 生成带语言后缀的文件名
            language_suffix = target_language.lower()
            srt_filename = f'translated_{language_suffix}.srt'
            srt_path = task_dir / srt_filename
            srt_path.write_text(srt_str, encoding='utf-8')
            print(f"翻译文件已保存到: {srt_filename}")
            
            return jsonify({
                "code": 0,
                "msg": "翻译保存成功",
                "srt_file": srt_filename
            })
            
        except Exception as e:
            print(f"保存翻译失败: {str(e)}")
            return jsonify({"code": 1, "msg": f"保存翻译失败: {str(e)}"}), 500
            
            # 同时保存JSON格式的翻译结果
            translation_data = {
                "task_id": task_id,
                "original_subtitles": subtitles,
                "translation_timestamp": datetime.now().isoformat()
            }
            translation_json_path = task_dir / f"{task_id}_translation.json"
            with open(translation_json_path, 'w', encoding='utf-8') as f:
                json.dump(translation_data, f, ensure_ascii=False, indent=2)
            
            print(f"翻译结果已保存: {translated_srt_path}")
            print(f"翻译数据已保存: {translation_json_path}")
            
            return jsonify({
                "code": 0,
                "msg": "翻译结果保存成功",
                "srt_file": str(translated_srt_path),
                "json_file": str(translation_json_path)
            })
            
        except Exception as e:
            print(f"保存翻译结果失败: {str(e)}")
            import traceback
            traceback.print_exc()
            return jsonify({"code": 1, "msg": f"保存失败: {str(e)}"}), 500

    @app.route('/viewer_api/<task_id>/voice_clone', methods=['POST'])
    def viewer_voice_clone(task_id):
        """语音克隆接口 - 使用ElevenLabs instant clone功能"""
        data = request.json
        if not data or 'speakers' not in data or 'subtitles' not in data:
            return jsonify({"code": 1, "msg": "缺少说话人或字幕数据"}), 400

        task_dir = Path(TARGET_DIR) / task_id
        if not task_dir.exists():
            return jsonify({"code": 1, "msg": "任务不存在"}), 404

        try:
            speakers = data['speakers']
            subtitles = data['subtitles']
            
            # 检查ElevenLabs API密钥
            if not config.params.get('elevenlabstts_key'):
                return jsonify({"code": 1, "msg": "未配置ElevenLabs API密钥"}), 400
            
            print(f"开始为 {len(speakers)} 个说话人进行语音克隆: {speakers}")
            
            # 首先删除所有现有的自定义语音，避免达到限制
            print("正在删除现有的自定义语音...")
            delete_success = delete_all_custom_voices()
            if not delete_success:
                print("警告：删除现有语音失败，但继续尝试创建新语音")
            
            # 创建语音克隆结果存储
            voice_clones = []
            voice_mapping = {}
            
            # 为每个说话人创建语音克隆
            for speaker in speakers:
                try:
                    print(f"正在为说话人 '{speaker}' 创建语音克隆...")
                    
                    # 获取该说话人的所有音频片段
                    speaker_segments = [s for s in subtitles if s.get('speaker', '').strip() == speaker]
                    if not speaker_segments:
                        print(f"说话人 '{speaker}' 没有找到音频片段")
                        continue
                    
                    # 提取该说话人的音频片段
                    speaker_audio_path = extract_speaker_audio(task_dir, speaker, speaker_segments)
                    if not speaker_audio_path:
                        print(f"无法提取说话人 '{speaker}' 的音频")
                        continue
                    
                    # 调用ElevenLabs instant clone API
                    clone_result = create_voice_clone(speaker, speaker_audio_path)
                    if clone_result:
                        voice_clone_info = {
                            "speaker": speaker,
                            "voice_id": clone_result.get('voice_id'),
                            "voice_name": clone_result.get('name'),
                            "audio_segments_count": len(speaker_segments),
                            "audio_file": str(speaker_audio_path)
                        }
                        voice_clones.append(voice_clone_info)
                        voice_mapping[speaker] = clone_result.get('voice_id')
                        print(f"说话人 '{speaker}' 语音克隆成功，voice_id: {clone_result.get('voice_id')}")
                    else:
                        print(f"说话人 '{speaker}' 语音克隆失败")
                        
                except Exception as e:
                    print(f"为说话人 '{speaker}' 创建语音克隆时出错: {str(e)}")
                    continue
            
            # 保存语音克隆映射关系
            if voice_mapping:
                mapping_file = task_dir / f"{task_id}_voice_mapping.json"
                mapping_data = {
                    "task_id": task_id,
                    "voice_mapping": voice_mapping,
                    "voice_clones": voice_clones,
                    "created_at": datetime.now().isoformat()
                }
                with open(mapping_file, 'w', encoding='utf-8') as f:
                    json.dump(mapping_data, f, ensure_ascii=False, indent=2)
                print(f"语音克隆映射关系已保存: {mapping_file}")
            
            return jsonify({
                "code": 0,
                "msg": f"语音克隆完成，成功为 {len(voice_clones)} 个说话人创建了语音克隆",
                "voice_clones": voice_clones,
                "voice_mapping": voice_mapping
            })
            
        except Exception as e:
            print(f"语音克隆失败: {str(e)}")
            import traceback
            traceback.print_exc()
            return jsonify({"code": 1, "msg": f"语音克隆失败: {str(e)}"}), 500

    def extract_speaker_audio(task_dir, speaker, speaker_segments):
        """提取指定说话人的音频片段 - 新流程：先切分再Demucs"""
        try:
            from videotrans.util import tools
            
            # 创建说话人音频目录
            speaker_dir = task_dir / "speaker_audio"
            speaker_dir.mkdir(exist_ok=True)
            
            # 获取视频文件路径
            video_files = list(task_dir.glob("*.mp4")) + list(task_dir.glob("*.avi")) + list(task_dir.glob("*.mov"))
            if not video_files:
                print("未找到视频文件")
                return None
            
            video_path = video_files[0]
            print(f"使用视频文件: {video_path}")
            
            # 第一步：根据SRT时间和说话人切分音频，生成 _spk[i] 文件
            speaker_audio_path = speaker_dir / f"spk{speaker.replace('spk', '')}.wav"
            if not speaker_audio_path.exists():
                print(f"正在切分说话人 '{speaker}' 的音频片段...")
                print(f"说话人片段数量: {len(speaker_segments)}")
                if speaker_segments:
                    print(f"第一个片段示例: {speaker_segments[0]}")
                
                # 合并该说话人的所有音频片段
                segment_files = []
                for i, segment in enumerate(speaker_segments):
                    # 支持多种字段名格式
                    start_time = (segment.get('start_time', segment.get('start', 0))) / 1000  # 转换为秒
                    end_time = (segment.get('end_time', segment.get('end', 0))) / 1000
                    duration = end_time - start_time
                    
                    print(f"片段 {i}: start={start_time}s, end={end_time}s, duration={duration}s")
                    
                    if duration > 0:
                        segment_file = speaker_dir / f"{speaker}_segment_{i}.wav"
                        tools.runffmpeg([
                            '-y', '-i', str(video_path),
                            '-ss', str(start_time), '-t', str(duration),
                            '-vn', '-acodec', 'pcm_s16le', '-ar', '44100', '-ac', '2',
                            str(segment_file)
                        ])
                        segment_files.append(str(segment_file))
                
                # 合并所有片段为 _spk[i] 文件
                if segment_files:
                    concat_file = speaker_dir / f"{speaker}_concat.txt"
                    with open(concat_file, 'w') as f:
                        for seg_file in segment_files:
                            f.write(f"file '{seg_file}'\n")
                    
                    tools.runffmpeg([
                        '-y', '-f', 'concat', '-safe', '0', '-i', str(concat_file),
                        '-acodec', 'pcm_s16le', '-ar', '44100', '-ac', '2',
                        str(speaker_audio_path)
                    ])
                    
                    # 清理临时文件
                    for seg_file in segment_files:
                        Path(seg_file).unlink(missing_ok=True)
                    concat_file.unlink(missing_ok=True)
                    
                    print(f"说话人 '{speaker}' 音频切分完成: {speaker_audio_path}")
                else:
                    print(f"说话人 '{speaker}' 没有有效的音频片段")
                    return None
            
            # 第二步：对 _spk[i] 文件用Demucs去背景音，生成 _vocal_spk[i] 文件
            vocal_audio_path = speaker_dir / f"vocal_spk{speaker.replace('spk', '')}.wav"
            if not vocal_audio_path.exists():
                print(f"正在使用Demucs分离人声: {vocal_audio_path}")
                
                # 使用Demucs分离人声
                success = separate_voice_background_demucs(str(speaker_audio_path), str(speaker_dir))
                
                if success:
                    # Demucs生成的文件名是background.wav和vocal.wav
                    demucs_vocal_path = speaker_dir / "vocal.wav"
                    if demucs_vocal_path.exists():
                        # 复制到我们期望的文件名 _vocal_spk[i]
                        import shutil
                        shutil.copy2(demucs_vocal_path, vocal_audio_path)
                        print(f"Demucs人声分离成功: {vocal_audio_path}")
                    else:
                        print("Demucs人声文件不存在，使用原始音频")
                        import shutil
                        shutil.copy2(speaker_audio_path, vocal_audio_path)
                else:
                    print("Demucs分离失败，使用原始音频")
                    import shutil
                    shutil.copy2(speaker_audio_path, vocal_audio_path)
            
            # 返回vocal文件路径用于声音克隆
            return vocal_audio_path if vocal_audio_path.exists() else None
            
        except Exception as e:
            print(f"提取说话人音频失败: {str(e)}")
            import traceback
            traceback.print_exc()
            return None

    def delete_all_custom_voices():
        """删除所有自定义语音"""
        try:
            from elevenlabs import ElevenLabs
            import httpx
            
            # 获取API密钥
            api_key = config.params.get('elevenlabstts_key')
            if not api_key:
                raise Exception("ElevenLabs API密钥未配置")
            
            # 创建客户端
            client = ElevenLabs(api_key=api_key, httpx_client=httpx.Client())
            
            # 获取所有语音
            voices = client.voices.get_all()
            custom_voices = [voice for voice in voices.voices if voice.category == 'cloned']
            
            print(f"找到 {len(custom_voices)} 个自定义语音，开始删除...")
            
            deleted_count = 0
            for voice in custom_voices:
                try:
                    client.voices.delete(voice.voice_id)
                    print(f"已删除语音: {voice.name} (ID: {voice.voice_id})")
                    deleted_count += 1
                except Exception as e:
                    print(f"删除语音失败 {voice.name}: {str(e)}")
            
            print(f"成功删除 {deleted_count} 个自定义语音")
            return True
            
        except Exception as e:
            print(f"删除自定义语音失败: {str(e)}")
            return False

    def create_voice_clone(speaker, audio_path):
        """使用ElevenLabs instant clone API创建语音克隆"""
        try:
            from elevenlabs import ElevenLabs
            import httpx
            from io import BytesIO
            
            # 创建ElevenLabs客户端
            client = ElevenLabs(
                api_key=config.params['elevenlabstts_key'],
                httpx_client=httpx.Client()
            )
            
            # 读取音频文件
            with open(audio_path, 'rb') as f:
                audio_data = f.read()
            
            # 创建语音克隆
            voice_name = f"{speaker}_clone_{int(time.time())}"
            
            # 使用instant voice cloning API
            voice = client.voices.ivc.create(
                name=voice_name,
                files=[BytesIO(audio_data)]
            )
            
            return {
                "voice_id": voice.voice_id,
                "name": voice_name,
                "speaker": speaker
            }
            
        except Exception as e:
            print(f"创建语音克隆失败: {str(e)}")
            return None

    @app.route('/viewer_api/<task_id>/check_generated_audio', methods=['GET'])
    def viewer_check_generated_audio(task_id):
        """检查是否有已生成的音频文件"""
        try:
            # 检查任务目录
            task_dir = Path(TARGET_DIR) / task_id
            if not task_dir.exists():
                return jsonify({"code": 1, "msg": "任务目录不存在", "has_audio": False})
            
            # 检查音频目录
            audio_dir = task_dir / "generated_audio"
            if not audio_dir.exists():
                return jsonify({"code": 0, "msg": "没有音频目录", "has_audio": False})
            
            # 查找音频文件
            audio_files = list(audio_dir.glob("segment_*.wav"))
            has_audio = len(audio_files) > 0
            
            return jsonify({
                "code": 0,
                "msg": "检查完成",
                "has_audio": has_audio,
                "audio_count": len(audio_files)
            })
            
        except Exception as e:
            return jsonify({"code": 1, "msg": f"检查失败: {str(e)}", "has_audio": False})

    @app.route('/viewer_api/<task_id>/check_voice_mapping', methods=['GET'])
    def viewer_check_voice_mapping(task_id):
        """检查语音克隆映射是否存在"""
        try:
            task_dir = Path(TARGET_DIR) / task_id
            if not task_dir.exists():
                return jsonify({"code": 1, "msg": "任务不存在"}), 404
            
            mapping_file = task_dir / f"{task_id}_voice_mapping.json"
            has_mapping = mapping_file.exists()
            
            return jsonify({
                "code": 0,
                "has_mapping": has_mapping,
                "mapping_file": str(mapping_file) if has_mapping else None
            })
            
        except Exception as e:
            return jsonify({"code": 1, "msg": f"检查失败: {str(e)}"}), 500

    @app.route('/viewer_api/<task_id>/synthesize_audio', methods=['POST'])
    def viewer_synthesize_audio(task_id):
        """合成音频接口 - 直接合成已生成的音频文件"""
        data = request.json
        if not data or 'subtitles' not in data:
            return jsonify({"code": 1, "msg": "缺少字幕数据"}), 400
        
        try:
            subtitles = data['subtitles']
            if not subtitles:
                return jsonify({"code": 1, "msg": "字幕数据为空"}), 400
            
            # 检查任务目录
            task_dir = Path(TARGET_DIR) / task_id
            if not task_dir.exists():
                return jsonify({"code": 1, "msg": "任务目录不存在"}), 400
            
            # 检查是否有已生成的音频文件
            audio_dir = task_dir / "generated_audio"
            if not audio_dir.exists():
                return jsonify({"code": 1, "msg": "没有找到已生成的音频文件"}), 400
            
            # 查找所有音频片段文件
            audio_files = []
            print(f"接收到的字幕数据: {len(subtitles)} 条")
            print(f"第一条字幕数据: {subtitles[0] if subtitles else 'None'}")
            
            for i, subtitle in enumerate(subtitles):
                # 检查必需字段，支持两种字段名
                start_time = subtitle.get('start_time')
                if start_time is None:
                    start_time = subtitle.get('start')
                    
                end_time = subtitle.get('end_time')
                if end_time is None:
                    end_time = subtitle.get('end')
                
                if start_time is None or end_time is None:
                    print(f"警告：字幕 {i+1} 缺少时间字段: {subtitle}")
                    continue
                    
                segment_file = audio_dir / f"segment_{i+1:04d}.wav"
                if segment_file.exists():
                    audio_files.append({
                        'start_time': start_time,
                        'end_time': end_time,
                        'file': str(segment_file)
                    })
                else:
                    print(f"警告：音频文件不存在: {segment_file}")
            
            if not audio_files:
                return jsonify({"code": 1, "msg": "没有找到有效的音频文件"}), 400
            
            print(f"找到 {len(audio_files)} 个音频文件，开始合成...")
            
            # 计算总时长
            total_duration = max(segment['end_time'] for segment in audio_files)
            
            # 合成完整音频
            final_audio_file = audio_dir / f"{task_id}_synthesized_audio.wav"
            success = synthesize_final_audio(audio_files, final_audio_file, total_duration)
            
            if not success:
                return jsonify({"code": 1, "msg": "音频合成失败"}), 500
            
            # 检查文件是否生成成功
            if not final_audio_file.exists():
                return jsonify({"code": 1, "msg": "合成文件未生成"}), 500
            
            file_size = final_audio_file.stat().st_size
            print(f"音频合成成功: {final_audio_file} (大小: {file_size / 1024:.1f} KB)")
            
            return jsonify({
                "code": 0,
                "msg": "音频合成成功",
                "output_file": str(final_audio_file),
                "file_size": file_size,
                "segments_count": len(audio_files)
            })
            
        except Exception as e:
            print(f"合成音频失败: {str(e)}")
            import traceback
            traceback.print_exc()
            return jsonify({"code": 1, "msg": f"合成音频失败: {str(e)}"}), 500

    @app.route('/viewer_api/<task_id>/generate_audio', methods=['POST'])
    def viewer_generate_audio(task_id):
        """生成音频接口 - 基于翻译字幕和语音克隆映射"""
        data = request.json
        if not data or 'subtitles' not in data:
            return jsonify({"code": 1, "msg": "缺少字幕数据"}), 400

        task_dir = Path(TARGET_DIR) / task_id
        if not task_dir.exists():
            return jsonify({"code": 1, "msg": "任务不存在"}), 404

        try:
            subtitles = data['subtitles']
            
            # 检查语音克隆映射文件
            mapping_file = task_dir / f"{task_id}_voice_mapping.json"
            if not mapping_file.exists():
                return jsonify({"code": 1, "msg": "未找到语音克隆映射文件，请先进行语音克隆"}), 400
            
            # 读取语音克隆映射
            with open(mapping_file, 'r', encoding='utf-8') as f:
                mapping_data = json.load(f)
            
            voice_mapping = mapping_data.get('voice_mapping', {})
            if not voice_mapping:
                return jsonify({"code": 1, "msg": "语音克隆映射为空"}), 400
            
            print(f"开始生成音频，共 {len(subtitles)} 条字幕")
            print(f"语音映射: {voice_mapping}")
            
            # 创建音频生成目录
            audio_dir = task_dir / "generated_audio"
            audio_dir.mkdir(exist_ok=True)
            
            # 为每条字幕生成TTS音频
            generated_audio_files = []
            total_duration = 0
            
            for i, subtitle in enumerate(subtitles):
                try:
                    speaker = subtitle.get('speaker', '').strip()
                    translated_text = subtitle.get('translated_text', '').strip()
                    start_time = subtitle.get('start_time', 0)
                    end_time = subtitle.get('end_time', 0)
                    duration = end_time - start_time
                    
                    if not translated_text:
                        print(f"字幕 {i+1} 没有翻译内容，跳过")
                        continue
                    
                    if not speaker or speaker not in voice_mapping:
                        print(f"字幕 {i+1} 说话人 '{speaker}' 没有对应的语音克隆，跳过")
                        continue
                    
                    voice_id = voice_mapping[speaker]
                    print(f"为字幕 {i+1} 生成TTS: 说话人={speaker}, voice_id={voice_id}")
                    
                    # 生成TTS音频
                    audio_file = audio_dir / f"segment_{i+1:04d}.wav"
                    success = generate_tts_audio(translated_text, voice_id, audio_file)
                    
                    if success:
                        generated_audio_files.append({
                            "file": str(audio_file),
                            "start_time": start_time,
                            "end_time": end_time,
                            "duration": duration,
                            "speaker": speaker,
                            "text": translated_text
                        })
                        total_duration = max(total_duration, end_time)
                        print(f"字幕 {i+1} TTS生成成功: {audio_file}")
                    else:
                        print(f"字幕 {i+1} TTS生成失败")
                        
                except Exception as e:
                    print(f"处理字幕 {i+1} 时出错: {str(e)}")
                    continue
            
            if not generated_audio_files:
                return jsonify({"code": 1, "msg": "没有成功生成任何音频片段"}), 500
            
            # 合成完整音频
            final_audio_file = audio_dir / f"{task_id}_final_audio.wav"
            success = synthesize_final_audio(generated_audio_files, final_audio_file, total_duration, regen_opts={"voice_mapping": voice_mapping})
            
            if not success:
                return jsonify({"code": 1, "msg": "音频合成失败"}), 500
            
            print(f"音频生成完成: {final_audio_file}")
            print(f"总时长: {total_duration/1000:.2f}秒")
            
            return jsonify({
                "code": 0,
                "msg": f"音频生成完成，共生成 {len(generated_audio_files)} 个音频片段",
                "audio_file": str(final_audio_file),
                "duration": f"{total_duration/1000:.2f}秒",
                "segments_count": len(generated_audio_files),
                "generated_segments": generated_audio_files
            })
            
        except Exception as e:
            print(f"生成音频失败: {str(e)}")
            import traceback
            traceback.print_exc()
            return jsonify({"code": 1, "msg": f"生成音频失败: {str(e)}"}), 500

    @app.route('/viewer_api/<task_id>/add_subtitles', methods=['POST'])
    def viewer_add_subtitles(task_id):
        """为指定视频烧录字幕（ASS），支持字体大小与底部距离百分比设置。

        请求体: {
            video_url: string 目标视频URL（原视频或当前播放的视频）
            font_size: int 字体大小（像素）
            bottom_percent: int 距离底部百分比（0-40）
            subtitles: [{start_time:int(ms), end_time:int(ms), text:str}, ...]
        }
        返回: {code:0, output_url:string}
        """
        try:
            data = request.get_json(silent=True) or {}
            video_url = data.get('video_url', '')
            font_size = int(data.get('font_size', 72))
            bottom_percent = max(0, min(40, int(data.get('bottom_percent', 20))))
            items = data.get('subtitles', [])
            subtitle_file_url = (data.get('subtitle_file') or '').strip()

            if not video_url:
                return jsonify({"code": 1, "msg": "缺少 video_url"}), 400
            if not subtitle_file_url and (not isinstance(items, list) or len(items) < 1):
                return jsonify({"code": 1, "msg": "缺少字幕数据"}), 400

            # 将 video_url 映射到本地路径
            # 允许 full url 或 '/apidata/...'
            src_path = None
            try:
                # 截取 '/apidata/' 之后的相对路径
                marker = f'/{API_RESOURCE}/'
                if marker in video_url:
                    rel = video_url.split(marker, 1)[1]
                    src_path = Path(TARGET_DIR) / rel
                else:
                    # 尝试当作相对路径
                    if video_url.startswith('/'):
                        src_path = Path(TARGET_DIR) / video_url.lstrip('/').split(f'{API_RESOURCE}/')[-1]
                    else:
                        src_path = Path(video_url)
            except Exception:
                pass

            if not src_path or not src_path.exists():
                return jsonify({"code": 1, "msg": "目标视频不存在或不可访问"}), 400

            # 探测分辨率
            import subprocess
            probe = subprocess.run([
                'ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height',
                '-of', 'csv=s=x:p=0', str(src_path)
            ], capture_output=True, text=True)
            if probe.returncode != 0 or 'x' not in probe.stdout:
                return jsonify({"code": 1, "msg": "无法探测视频分辨率"}), 500
            w, h = [int(x) for x in probe.stdout.strip().split('x')]

            # 输出文件（写入当前任务目录）
            out_path = Path(TARGET_DIR) / task_id / f"subtitled_{int(time.time())}.mp4"
            from videotrans.util import tools
            margin_lr = int(w * 0.1)  # 左右各10%，总80%有效宽度
            margin_v = int(h * (bottom_percent / 100.0))

            if subtitle_file_url:
                # 使用已有字幕文件。如果是 .ass 直接烧录；如果是 .srt/.vtt，则先转换为 ASS（应用样式），再烧录。
                marker = f'/{API_RESOURCE}/'
                if marker in subtitle_file_url:
                    rel = subtitle_file_url.split(marker, 1)[1]
                    sub_src = Path(TARGET_DIR) / rel
                else:
                    sub_src = Path(subtitle_file_url)
                if not sub_src.exists():
                    return jsonify({"code": 1, "msg": "字幕文件不存在"}), 400

                if sub_src.suffix.lower() == '.ass':
                    # 强制样式以实现 80% 宽度与居中、自动换行
                    force_style = f"WrapStyle=2,Alignment=2,MarginL={margin_lr},MarginR={margin_lr},MarginV={margin_v}"
                    vf = f"subtitles=filename={sub_src.as_posix()}:force_style='{force_style}'"
                    cmd = ['-y', '-i', str(src_path), '-vf', vf, '-loglevel', 'info', '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-c:a', 'copy', str(out_path)]
                    print("[AddSubtitles] FFmpeg command:", 'ffmpeg', *cmd)
                    try:
                        from videotrans.util import tools as _t
                        _t.set_process(text=f"FFmpeg: ffmpeg {' '.join(cmd)}", uuid=task_id)
                    except Exception:
                        pass
                    tools.runffmpeg(cmd)
                else:
                    # 解析 SRT/VTT -> items，然后走统一的 ASS 生成逻辑
                    try:
                        items = parse_srt_file_to_items(sub_src)
                    except Exception:
                        items = []
                    if not items:
                        return jsonify({"code": 1, "msg": "无法解析字幕文件"}), 400
                    # 下面与无文件分支相同：根据 items 生成 ASS，并走 FFmpeg 烧录
                    ass_path = Path(TARGET_DIR) / task_id / f"subtitles_{int(time.time())}.ass"
                    ass_path.parent.mkdir(parents=True, exist_ok=True)
                    def fmt_time(ms: int) -> str:
                        ms = max(0, int(ms))
                        cs = int((ms % 1000) / 10)
                        s = ms // 1000
                        hh = s // 3600
                        mm = (s % 3600) // 60
                        ss = s % 60
                        return f"{hh}:{mm:02d}:{ss:02d}.{cs:02d}"
                    def wrap_text_for_ass(t: str, max_chars: int) -> str:
                        """按最大字符数粗略换行，尽量按空格断行；无空格则按字符数断行。"""
                        t = (t or '').replace('\r', '')
                        lines = []
                        for raw_line in t.split('\n'):
                            s = raw_line.strip()
                            if not s:
                                lines.append('')
                                continue
                            words = s.split(' ')
                            if len(words) > 1:
                                cur = ''
                                for w2 in words:
                                    if cur == '':
                                        nxt = w2
                                    else:
                                        nxt = cur + ' ' + w2
                                    if len(nxt) <= max_chars:
                                        cur = nxt
                                    else:
                                        if cur:
                                            lines.append(cur)
                                        cur = w2
                                if cur:
                                    lines.append(cur)
                            else:
                                # 无空格，按字符数硬切
                                s2 = s
                                while len(s2) > max_chars:
                                    lines.append(s2[:max_chars])
                                    s2 = s2[max_chars:]
                                if s2:
                                    lines.append(s2)
                        return '\\N'.join(lines)

                    def esc_text(t: str) -> str:
                        # 先换行，再做转义；保留 \N 作为换行
                        t2 = t or ''
                        max_text_width = int(w * 0.8)
                        est_char_w = max(1, int(font_size * 0.6))
                        max_chars = max(8, int(max_text_width / est_char_w))
                        t2 = wrap_text_for_ass(t2, max_chars)
                        placeholder = '<<__ASS_NL__>>'
                        t2 = t2.replace('\\N', placeholder)
                        t2 = t2.replace('\\', '\\\\').replace('{', '(').replace('}', ')')
                        t2 = t2.replace(placeholder, '\\N')
                        return t2
                    ass_lines = []
                    ass_lines.append('[Script Info]')
                    ass_lines.append('ScriptType: v4.00+')
                    ass_lines.append(f'PlayResX: {w}')
                    ass_lines.append(f'PlayResY: {h}')
                    ass_lines.append('WrapStyle: 2')
                    ass_lines.append('ScaledBorderAndShadow: yes')
                    ass_lines.append('')
                    ass_lines.append('[V4+ Styles]')
                    ass_lines.append('Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, '
                                      'Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, '
                                      'Shadow, Alignment, MarginL, MarginR, MarginV, Encoding')
                    ass_lines.append(
                        f"Style: Default,Arial,{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,3,0,2,{margin_lr},{margin_lr},{margin_v},0"
                    )
                    ass_lines.append('')
                    ass_lines.append('[Events]')
                    ass_lines.append('Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text')
                    for it in items:
                        st = fmt_time(int(it.get('start_time', 0)))
                        et = fmt_time(int(it.get('end_time', 0)))
                        txt = esc_text(str(it.get('text', '')))
                        ass_lines.append(f"Dialogue: 0,{st},{et},Default,,0000,0000,0000,,{txt}")
                    ass_path.write_text('\n'.join(ass_lines), encoding='utf-8')
                    vf = f"subtitles=filename={ass_path.as_posix()}"
                    cmd = ['-y', '-i', str(src_path), '-vf', vf, '-loglevel', 'info', '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-c:a', 'copy', str(out_path)]
                    print("[AddSubtitles] FFmpeg command:", 'ffmpeg', *cmd)
                    try:
                        from videotrans.util import tools as _t
                        _t.set_process(text=f"FFmpeg: ffmpeg {' '.join(cmd)}", uuid=task_id)
                    except Exception:
                        pass
                    tools.runffmpeg(cmd)
            else:
                # 生成 ASS 文件后烧录
                ass_path = Path(TARGET_DIR) / task_id / f"subtitles_{int(time.time())}.ass"
                ass_path.parent.mkdir(parents=True, exist_ok=True)

                def fmt_time(ms: int) -> str:
                    ms = max(0, int(ms))
                    cs = int((ms % 1000) / 10)  # centiseconds
                    s = ms // 1000
                    hh = s // 3600
                    mm = (s % 3600) // 60
                    ss = s % 60
                    return f"{hh}:{mm:02d}:{ss:02d}.{cs:02d}"

                def wrap_text_for_ass(t: str, max_chars: int) -> str:
                    t = (t or '').replace('\r', '')
                    lines = []
                    for raw_line in t.split('\n'):
                        s = raw_line.strip()
                        if not s:
                            lines.append('')
                            continue
                        words = s.split(' ')
                        if len(words) > 1:
                            cur = ''
                            for w2 in words:
                                if cur == '':
                                    nxt = w2
                                else:
                                    nxt = cur + ' ' + w2
                                if len(nxt) <= max_chars:
                                    cur = nxt
                                else:
                                    if cur:
                                        lines.append(cur)
                                    cur = w2
                            if cur:
                                lines.append(cur)
                        else:
                            s2 = s
                            while len(s2) > max_chars:
                                lines.append(s2[:max_chars])
                                s2 = s2[max_chars:]
                            if s2:
                                lines.append(s2)
                    return '\\N'.join(lines)

                def esc_text(t: str) -> str:
                    # 预换行再转义；保留 \N 作为换行
                    max_text_width = int(w * 0.8)
                    est_char_w = max(1, int(font_size * 0.6))
                    max_chars = max(8, int(max_text_width / est_char_w))
                    t2 = wrap_text_for_ass(t or '', max_chars)
                    placeholder = '<<__ASS_NL__>>'
                    t2 = t2.replace('\\N', placeholder)
                    t2 = t2.replace('\\', '\\\\').replace('{', '(').replace('}', ')')
                    t2 = t2.replace(placeholder, '\\N')
                    return t2

                ass_lines = []
                ass_lines.append('[Script Info]')
                ass_lines.append('ScriptType: v4.00+')
                ass_lines.append(f'PlayResX: {w}')
                ass_lines.append(f'PlayResY: {h}')
                ass_lines.append('WrapStyle: 2')
                ass_lines.append('ScaledBorderAndShadow: yes')
                ass_lines.append('')
                ass_lines.append('[V4+ Styles]')
                ass_lines.append('Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, '
                                  'Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, '
                                  'Shadow, Alignment, MarginL, MarginR, MarginV, Encoding')
                ass_lines.append(
                    f"Style: Default,Arial,{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,3,0,2,{margin_lr},{margin_lr},{margin_v},0"
                )
                ass_lines.append('')
                ass_lines.append('[Events]')
                ass_lines.append('Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text')
                for it in items:
                    st = fmt_time(int(it.get('start_time', 0)))
                    et = fmt_time(int(it.get('end_time', 0)))
                    txt = esc_text(str(it.get('text', '')))
                    ass_lines.append(f"Dialogue: 0,{st},{et},Default,,0000,0000,0000,,{txt}")

                ass_path.write_text('\n'.join(ass_lines), encoding='utf-8')

                vf = f"subtitles=filename={ass_path.as_posix()}"
                cmd = ['-y', '-i', str(src_path), '-vf', vf, '-loglevel', 'info', '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-c:a', 'copy', str(out_path)]
                # 打印与记录 FFmpeg 命令，便于排查
                print("[AddSubtitles] FFmpeg command:", 'ffmpeg', *cmd)
                try:
                    from videotrans.util import tools as _t
                    _t.set_process(text=f"FFmpeg: ffmpeg {' '.join(cmd)}", uuid=task_id)
                except Exception:
                    pass
                try:
                    tools.runffmpeg(cmd)
                except Exception:
                    print('[AddSubtitles] FFmpeg failed. Fallback to OpenCV burn (items).')
                    ok3 = burn_subtitles_with_opencv(
                        str(src_path), items, str(out_path),
                        font_size=font_size, bottom_percent=bottom_percent
                    )
                    if not ok3:
                        raise

            if not out_path.exists():
                return jsonify({"code": 1, "msg": "字幕烧录失败"}), 500

            return jsonify({
                "code": 0,
                "msg": "ok",
                "output_url": f'/{API_RESOURCE}/{task_id}/{out_path.name}'
            })

        except Exception as e:
            print(f"添加字幕失败: {str(e)}")
            import traceback
            traceback.print_exc()
            return jsonify({"code": 1, "msg": f"添加字幕失败: {str(e)}"}), 500

    @app.route('/viewer_api/<task_id>/list_subtitle_files', methods=['GET'])
    def viewer_list_subtitle_files(task_id):
        """列出任务目录下可用的字幕文件（srt/ass/vtt/json）。"""
        try:
            task_dir = Path(TARGET_DIR) / task_id
            if not task_dir.exists():
                return jsonify({"code": 1, "msg": "任务不存在", "files": []}), 404

            exts = {'.srt', '.ass', '.vtt', '.json'}
            files = []
            for f in sorted(task_dir.iterdir()):
                if f.is_file() and f.suffix.lower() in exts:
                    files.append({
                        'name': f.name,
                        'url': f'/{API_RESOURCE}/{task_id}/{f.name}'
                    })
            return jsonify({"code": 0, "files": files})
        except Exception as e:
            return jsonify({"code": 1, "msg": f"列出失败: {str(e)}", "files": []}), 500

    def parse_srt_file_to_items(srt_path):
        """将 SRT 文件解析为 items 列表: [{start_time,end_time,text}]"""
        try:
            content = Path(srt_path).read_text(encoding='utf-8')
        except Exception:
            content = Path(srt_path).read_text(encoding='latin-1')

        import re
        blocks = re.split(r'\n\s*\n', content.strip())
        items = []
        def t2ms(t):
            # 00:00:00,000 或 00:00:00.000
            t = t.replace('.', ',')
            h, m, s_ms = t.split(':')
            s, ms = s_ms.split(',')
            return (int(h)*3600 + int(m)*60 + int(s))*1000 + int(ms)
        for b in blocks:
            lines = [ln for ln in b.splitlines() if ln.strip()]
            if len(lines) < 2:
                continue
            # 找到时间行
            time_idx = None
            for i, ln in enumerate(lines[:3]):
                if '-->' in ln:
                    time_idx = i
                    break
            if time_idx is None:
                continue
            time_line = lines[time_idx]
            text_lines = lines[time_idx+1:]
            l, r = [t.strip() for t in time_line.split('-->')]
            st = t2ms(l)
            et = t2ms(r)
            text = '\n'.join(text_lines)
            items.append({'start_time': st, 'end_time': et, 'text': text})
        return items

    def _find_font_path_candidates():
        return [
            '/System/Library/Fonts/Supplemental/Arial.ttf',
            '/System/Library/Fonts/Supplemental/Arial Unicode.ttf',
            '/Library/Fonts/Arial.ttf',
            '/Library/Fonts/Arial Unicode.ttf',
            '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
            'C:/Windows/Fonts/arial.ttf',
        ]

    def burn_subtitles_with_opencv(video_path, items, output_path, font_size=72, bottom_percent=20, width_ratio=0.8):
        """使用 OpenCV+Pillow 将给定字幕事件烧录到视频。

        items: list of {start_time(ms), end_time(ms), text}
        输出先生成无音频视频，再用 ffmpeg 将原视频音频复用到输出。
        """
        try:
            import cv2
            import numpy as np
            from PIL import Image, ImageDraw, ImageFont
        except Exception as e:
            print(f'缺少依赖: {e}. 需要安装 opencv-python 与 pillow')
            return False

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            print('无法打开视频:', video_path)
            return False

        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        out_noaudio = Path(output_path).with_suffix('.noaudio.mp4')
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        writer = cv2.VideoWriter(out_noaudio.as_posix(), fourcc, fps, (width, height))
        if not writer.isOpened():
            print('无法创建输出视频:', out_noaudio)
            cap.release()
            return False

        # 字体
        font_path = None
        for p in _find_font_path_candidates():
            if Path(p).exists():
                font_path = p
                break
        try:
            font = ImageFont.truetype(font_path or 'Arial', max(12, int(font_size)))
        except Exception:
            font = ImageFont.load_default()

        max_text_width = int(width * width_ratio)
        margin_v = int(height * (bottom_percent / 100.0))

        def wrap_text(txt, draw):
            # 按宽度换行（逐字符，兼容中西文）
            parts = txt.replace('\r', '').split('\n')
            lines = []
            for block in parts:
                line = ''
                for ch in block:
                    test = line + ch
                    w, _ = draw.textsize(test, font=font)
                    if w <= max_text_width:
                        line = test
                    else:
                        if line:
                            lines.append(line)
                        line = ch
                if line:
                    lines.append(line)
            return lines

        items_sorted = sorted(items, key=lambda x: x.get('start_time', 0))
        idx = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            cur_ms = int(idx * 1000.0 / fps)

            # 当前字幕
            txt = ''
            for it in items_sorted:
                if it.get('start_time', 0) <= cur_ms < it.get('end_time', 0):
                    txt = it.get('text', '')
                if it.get('start_time', 0) > cur_ms:
                    break

            if txt:
                img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                draw = ImageDraw.Draw(img)
                lines = wrap_text(txt, draw)
                # 文本块高度估计
                bbox = font.getbbox('Hg')
                lh = bbox[3] - bbox[1]
                total_h = int(len(lines) * lh * 1.2)
                y0 = height - margin_v - total_h
                for i2, line in enumerate(lines):
                    w_text, _ = draw.textsize(line, font=font)
                    x = int((width - w_text) / 2)
                    y = int(y0 + i2 * lh * 1.2)
                    draw.text((x, y), line, font=font, fill=(255,255,255,255), stroke_width=3, stroke_fill=(0,0,0,255))
                frame = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)

            writer.write(frame)
            idx += 1

        writer.release()
        cap.release()

        # 复用原视频音频
        try:
            from videotrans.util import tools as _t
            mux_cmd = [
                '-y', '-i', out_noaudio.as_posix(), '-i', video_path,
                '-c:v', 'copy', '-map', '0:v:0', '-map', '1:a:0?', '-c:a', 'aac', '-b:a', '128k', '-shortest', output_path
            ]
            print('[AddSubtitles][OpenCV] FFmpeg mux audio:', 'ffmpeg', *mux_cmd)
            _t.runffmpeg(mux_cmd)
        except Exception as e:
            print('[AddSubtitles][OpenCV] 复用音频失败:', e)
            try:
                Path(output_path).unlink(missing_ok=True)
            except Exception:
                pass
            out_noaudio.replace(output_path)

        try:
            out_noaudio.unlink(missing_ok=True)
        except Exception:
            pass
        return Path(output_path).exists()

    def generate_tts_audio(text, voice_id, output_file, speaking_rate=None):
        """使用ElevenLabs生成TTS音频，支持可选语速speaking_rate（倍率）。"""
        try:
            from elevenlabs import ElevenLabs
            import httpx
            
            # 创建ElevenLabs客户端
            client = ElevenLabs(
                api_key=config.params['elevenlabstts_key'],
                httpx_client=httpx.Client()
            )
            
            kwargs = {
                'voice_id': voice_id,
                'text': text,
                'model_id': "eleven_flash_v2_5"
            }
            # 尝试传入语速设置（若SDK/模型不支持则会被忽略或抛错）
            if speaking_rate and speaking_rate > 0:
                try:
                    kwargs['voice_settings'] = {"speaking_rate": float(speaking_rate)}
                except Exception:
                    pass
            
            # 生成TTS音频
            audio = client.text_to_speech.convert(**kwargs)
            
            # 保存音频文件
            with open(output_file, 'wb') as f:
                for chunk in audio:
                    f.write(chunk)
            
            return True
            
        except Exception as e:
            print(f"TTS生成失败: {str(e)}")
            return False

    def adjust_audio_length_and_volume(audio_file, target_duration_ms, volume_boost=1.8):
        """调整音频长度与音量，并强制匹配SRT目标时长。

        - 自动计算变速比并用 atempo 调整（支持级联 atempo 以超出 0.5~2.0 范围）。
        - 提升音量（默认 1.8）。
        - 通过 apad + -t 精确修剪/补齐至目标时长。
        """
        try:
            from videotrans.util import tools
            from pathlib import Path as _Path
            import subprocess

            audio_path = _Path(audio_file)
            temp1 = audio_path.parent / f"temp_speedvol_{audio_path.name}"
            temp2 = audio_path.parent / f"temp_exact_{audio_path.name}"

            # 获取原始音频时长（秒）
            probe = subprocess.run([
                'ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
                '-of', 'csv=p=0', str(audio_path)
            ], capture_output=True, text=True)
            if probe.returncode != 0 or not probe.stdout.strip():
                print(f"无法获取音频时长: {audio_path}")
                return audio_path

            original_duration = float(probe.stdout.strip())
            target_duration = max(0.01, float(target_duration_ms) / 1000.0)
            print(f"原始时长: {original_duration:.3f}s, 目标时长: {target_duration:.3f}s")

            # 计算速度调整比例：>1 加速（缩短），<1 减速（拉长）。限制在 ±20% 内更自然
            raw_ratio = original_duration / target_duration if target_duration > 0 else 1.0
            speed_ratio = max(0.8, min(1.2, raw_ratio))

            # 构建 atempo 级联链，保证每段处于 [0.5, 2.0]
            def build_atempo_chain(ratio: float) -> str:
                chain = []
                r = ratio
                # 处理极端值，分段逼近
                while r > 2.0:
                    chain.append('atempo=2.0')
                    r /= 2.0
                while r < 0.5:
                    chain.append('atempo=0.5')
                    r /= 0.5
                # 最后一段（处于0.5~2.0）
                chain.append(f'atempo={r:.5f}')
                return ','.join(chain)

            if abs(speed_ratio - 1.0) < 0.01:
                print("时长差异很小，仅提升音量")
                tools.runffmpeg([
                    '-y', '-i', str(audio_path),
                    '-af', f'volume={volume_boost}',
                    '-ar', '44100', '-ac', '2', str(temp1)
                ])
            else:
                atempo_chain = build_atempo_chain(speed_ratio)
                print(f"使用受限变速链(±20%): {atempo_chain} (原始建议比率={raw_ratio:.3f})")
                tools.runffmpeg([
                    '-y', '-i', str(audio_path),
                    '-af', f'{atempo_chain},volume={volume_boost}',
                    '-ar', '44100', '-ac', '2', str(temp1)
                ])

            # 第二步：用 apad + -t 精确到目标长度
            tools.runffmpeg([
                '-y', '-i', str(temp1),
                '-af', 'apad', '-t', f'{target_duration:.6f}',
                '-ar', '44100', '-ac', '2', str(temp2)
            ])

            # 校验输出时长，必要时再精修剪/补齐
            probe2 = subprocess.run([
                'ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
                '-of', 'csv=p=0', str(temp2)
            ], capture_output=True, text=True)
            try:
                out_dur = float((probe2.stdout or '0').strip())
            except Exception:
                out_dur = 0.0
            if out_dur <= 0 or abs(out_dur - target_duration) > 0.01:
                # 再通过 atrim 精准修正
                print(f"输出时长偏差 {out_dur:.3f}s，目标 {target_duration:.3f}s，执行精修...")
                tmp_final = audio_path.parent / f"tmp_final_{audio_path.name}"
                tools.runffmpeg([
                    '-y', '-i', str(temp2),
                    '-af', f'atrim=0:{target_duration:.6f},asetpts=N/SR/TB',
                    '-ar', '44100', '-ac', '2', str(tmp_final)
                ])
                tmp_final.replace(audio_path)
            else:
                # 替换原文件
                temp2.replace(audio_path)
            # 清理临时文件
            try: temp1.unlink(missing_ok=True)
            except Exception: pass
            print(f"音频调整完成: {audio_path}")
            return audio_path

        except Exception as e:
            print(f"音频调整失败: {str(e)}")
            return audio_file

    def synthesize_final_audio(audio_segments, output_file, total_duration, regen_opts=None):
        """合成最终音频文件"""
        try:
            from videotrans.util import tools
            import subprocess
            from pathlib import Path as _Path

            regen_opts = regen_opts or {}
            voice_mapping = regen_opts.get('voice_mapping') or {}

            def _get_dur_sec(p: _Path) -> float:
                r = subprocess.run([
                    'ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
                    '-of', 'csv=p=0', str(p)
                ], capture_output=True, text=True)
                try:
                    return float((r.stdout or '0').strip())
                except Exception:
                    return 0.0
            
            # 创建静音文件作为基础 - 修复FFmpeg参数
            silence_file = output_file.parent / "silence.wav"
            tools.runffmpeg([
                '-y', '-f', 'lavfi', '-i', 'anullsrc',
                '-t', str(total_duration/1000),  # 使用-t参数指定时长
                '-ar', '44100', '-ac', '2', str(silence_file)
            ])
            
            # 为每个音频片段创建覆盖命令，只处理存在的文件
            filter_complex = []
            inputs = ['-i', str(silence_file)]  # 添加 -i 前缀
            valid_segments = []
            
            for i, segment in enumerate(audio_segments):
                start_time = segment['start_time'] / 1000  # 转换为秒
                end_time = segment['end_time'] / 1000
                # 兼容缺失 duration 的场景，回退为 end-start
                target_duration = int(segment.get('duration', segment['end_time'] - segment['start_time']))  # 毫秒
                audio_file = segment['file']
                
                # 检查文件是否存在
                if not Path(audio_file).exists():
                    print(f"警告：音频文件不存在，跳过: {audio_file}")
                    continue
                
                # 计算与目标的比例
                orig_sec = _get_dur_sec(Path(audio_file))
                tgt_sec = max(0.01, target_duration/1000.0)
                ratio = (orig_sec / tgt_sec) if tgt_sec > 0 else 1.0
                print(f"片段 {i+1} 原始={orig_sec:.3f}s 目标={tgt_sec:.3f}s 比例={ratio:.3f}")

                adjusted_path = Path(audio_file)
                need_regen = (ratio < 0.8 or ratio > 1.2) and bool(voice_mapping) and ('text' in segment) and ('speaker' in segment) and segment.get('speaker') in voice_mapping

                if need_regen:
                    # 超过±20%，优先尝试通过 ElevenLabs 以不同语速重生成
                    speaker = segment.get('speaker')
                    text = segment.get('text', '')
                    voice_id = voice_mapping.get(speaker)
                    if voice_id and text:
                        speaking_rate = max(0.5, min(2.0, (tgt_sec / orig_sec) if orig_sec > 0 else 1.0))
                        regen_file = Path(audio_file).parent / f"regen_{Path(audio_file).name}"
                        print(f"超出20%，尝试以语速 {speaking_rate:.3f} 重生成 ElevenLabs 片段...")
                        ok = generate_tts_audio(text, voice_id, regen_file, speaking_rate=speaking_rate)
                        if ok and regen_file.exists():
                            new_ratio = (_get_dur_sec(regen_file) / tgt_sec) if tgt_sec > 0 else 1.0
                            print(f"重生成结果时长比: {new_ratio:.3f}")
                            adjusted_path = regen_file
                        else:
                            print("重生成失败，退回到20%范围内的变速处理")
                            adjusted_path = adjust_audio_length_and_volume(adjusted_path, target_duration, volume_boost=1.8)
                    else:
                        adjusted_path = adjust_audio_length_and_volume(adjusted_path, target_duration, volume_boost=1.8)
                else:
                    # 在±20%内（或重生成不可用），用本地变速+增益对齐
                    adjusted_path = adjust_audio_length_and_volume(adjusted_path, target_duration, volume_boost=1.8)
                
                # 添加输入文件
                inputs.extend(['-i', str(adjusted_path)])
                
                # 记录有效的片段索引（从1开始，因为0是静音文件）
                current_index = len(valid_segments) + 1
                valid_segments.append({
                    'index': current_index,
                    'start_time': start_time,
                    'file': str(adjusted_path)
                })
                
                # 添加覆盖滤镜
                filter_complex.append(f"[{current_index}:a]adelay={int(start_time*1000)}|{int(start_time*1000)}[a{current_index}]")
            
            if not valid_segments:
                print("没有有效的音频片段，无法合成")
                return False
            
            # 合并所有音频
            mix_inputs = "[0:a]"
            for segment in valid_segments:
                mix_inputs += f"[a{segment['index']}]"
            mix_inputs += f"amix=inputs={len(valid_segments)+1}:duration=longest[out]"
            
            filter_complex.append(mix_inputs)
            
            # 构建FFmpeg命令
            cmd = ['-y'] + inputs + [
                '-filter_complex', ';'.join(filter_complex),
                '-map', '[out]',
                '-ar', '44100', '-ac', '2', '-b:a', '128k',
                str(output_file)
            ]
            
            tools.runffmpeg(cmd)
            
            # 清理临时文件
            silence_file.unlink(missing_ok=True)
            
            return output_file.exists()
            
        except Exception as e:
            print(f"音频合成失败: {str(e)}")
            return False

    def start_video_synthesis_task(task_id, video_path, subtitles):
        """启动视频合成任务的后台处理函数

        新流程（点击“合成视频”）：
        1) 将输入视频音视频分离，生成 task_dir/video_only.mp4 与 task_dir/audio_only.wav
        2) 对 audio_only.wav 进行 Demucs 分离，保留背景音为 task_dir/audio_background.wav
        3) 从 原任务目录/generated_audio 中查找带 final 后缀的已合成音频，与背景音混合生成 task_dir/final_audio.wav
        4) 使用 video_only.mp4 + final_audio.wav 合成 task_dir/result.mp4
        """
        try:
            from videotrans.util import tools
            import subprocess
            import shutil

            print(f"开始视频合成任务: {task_id}")
            tools.set_process(text='[0/4] 初始化任务...', uuid=task_id)

            # 任务目录（用于输出结果展示）
            task_dir = Path(TARGET_DIR) / task_id
            cache_dir = Path(config.TEMP_DIR) / task_id
            task_dir.mkdir(parents=True, exist_ok=True)
            cache_dir.mkdir(parents=True, exist_ok=True)

            src_task_dir = Path(video_path).parent  # 原始任务目录（用于查找 generated_audio）

            # Step 1: 音视频分离
            print("[1/4] 正在分离音视频...")
            tools.set_process(text='[1/4] 正在分离音视频...', uuid=task_id)
            video_only_path = task_dir / "video_only.mp4"
            audio_only_path = task_dir / "audio_only.wav"

            # 提取无声视频
            tools.runffmpeg([
                '-y', '-i', str(video_path),
                '-c:v', 'copy', '-an', str(video_only_path)
            ])

            # 提取音频（双声道、44100Hz、s16）
            tools.runffmpeg([
                '-y', '-i', str(video_path),
                '-vn', '-ac', '2', '-ar', '44100', '-sample_fmt', 's16', str(audio_only_path)
            ])

            if not video_only_path.exists() or not audio_only_path.exists():
                print("分离音视频失败：未生成 video_only 或 audio_only")
                return

            print(f"已生成: {video_only_path.name}, {audio_only_path.name}")

            # Step 2: Demucs 分离保留背景音
            print("[2/4] 正在使用 Demucs 分离背景音...")
            tools.set_process(text='[2/4] 正在分离背景音...', uuid=task_id)
            # 在任务目录下生成 background.wav / vocal.wav，然后重命名背景音为 audio_background.wav
            demucs_ok = separate_voice_background_demucs(str(audio_only_path), str(task_dir))
            bgm_source = task_dir / "background.wav"
            audio_background_path = task_dir / "audio_background.wav"
            if demucs_ok and bgm_source.exists():
                shutil.copy2(bgm_source, audio_background_path)
                print(f"背景音生成成功: {audio_background_path}")
            else:
                # 失败时按文档回退使用原音频作为背景音
                shutil.copy2(audio_only_path, audio_background_path)
                print("Demucs 分离失败或输出缺失，使用原音频作为背景音")

            # Step 3: 寻找 generated_audio 中的 final 音频并混合
            print("[3/4] 正在查找 generated_audio 中的 final 音频...")
            tools.set_process(text='[3/4] 正在混合人声与背景...', uuid=task_id)
            gen_dir = src_task_dir / "generated_audio"
            if not gen_dir.exists():
                print(f"未找到目录: {gen_dir}")
                return

            # 优先匹配包含 "final" 关键字的 wav，其次 m4a/mp3
            candidates = []
            for pat in ["*final*.wav", "*final*.m4a", "*final*.mp3", "*_final_audio.wav", "*_synthesized_audio.wav"]:
                candidates.extend(sorted(gen_dir.glob(pat)))

            # 去重并按修改时间倒序，选择最新的
            uniq = []
            seen = set()
            for p in candidates:
                if p.as_posix() not in seen:
                    seen.add(p.as_posix())
                    uniq.append(p)
            if not uniq:
                print("未找到带 final 后缀的已合成音频，请先生成合成音频")
                return

            uniq.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            tts_final_path = uniq[0]
            print(f"使用已合成音频: {tts_final_path}")

            final_audio_path = task_dir / "final_audio.wav"
            ok_mix = mix_audio_files(str(audio_background_path), str(tts_final_path), str(final_audio_path))
            if not ok_mix or not final_audio_path.exists():
                print("混合背景音与已合成音频失败")
                return
            print(f"已生成最终音频: {final_audio_path}")

            # Step 4: 合成最终视频
            print("[4/4] 正在合成最终视频 result.mp4 ...")
            tools.set_process(text='[4/4] 正在合成视频...', uuid=task_id)
            result_video_path = task_dir / "result.mp4"
            ok_video = combine_audio_with_video_simple(str(final_audio_path), str(video_only_path), str(result_video_path))
            if ok_video:
                print(f"视频合成完成: {result_video_path}")
                tools.set_process(text='合成完成', type='succeed', uuid=task_id)

                # 保存任务结果信息（用于结果页展示）
                result_info = {
                    "task_id": task_id,
                    "status": "completed",
                    "output_file": str(result_video_path),
                    "download_url": f'/{API_RESOURCE}/{task_id}/{result_video_path.name}'
                }
                result_file = task_dir / "result.json"
                with open(result_file, 'w', encoding='utf-8') as f:
                    json.dump(result_info, f, ensure_ascii=False, indent=2)
            else:
                print("视频合成失败：未生成 result.mp4")
                tools.set_process(text='视频合成失败：未生成 result.mp4', type='error', uuid=task_id)

        except Exception as e:
            print(f"视频合成任务失败: {str(e)}")
            import traceback
            traceback.print_exc()
            try:
                tools.set_process(text=f'合成失败：{str(e)}', type='error', uuid=task_id)
            except Exception:
                pass

    def mix_audio_files(bgm_path, tts_path, output_path):
        """混合背景音乐和TTS音频"""
        try:
            import subprocess
            
            # 检查输入文件
            bgm_file = Path(bgm_path)
            tts_file = Path(tts_path)
            
            if not bgm_file.exists():
                print(f"背景音乐文件不存在: {bgm_path}")
                return False
                
            if not tts_file.exists():
                print(f"TTS音频文件不存在: {tts_path}")
                return False
            
            print(f"背景音乐文件大小: {bgm_file.stat().st_size / 1024:.1f} KB")
            print(f"TTS音频文件大小: {tts_file.stat().st_size / 1024:.1f} KB")
            
            # 调整增益，提升整体响度：提升TTS与BGM音量，并关闭amix的normalize避免总体被压低
            cmd = [
                'ffmpeg', '-y',
                '-i', str(bgm_path),  # 背景音乐
                '-i', str(tts_path),  # TTS音频
                '-filter_complex',
                '[0:a]volume=0.5[bgm];[1:a]volume=1.4[tts];' \
                '[bgm][tts]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0[mixed]',
                '-map', '[mixed]',
                '-c:a', 'pcm_s16le',  # 使用PCM格式确保质量
                '-ar', '44100',       # 采样率
                str(output_path)
            ]
            
            print(f"执行FFmpeg命令: {' '.join(cmd)}")
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            
            output_file = Path(output_path)
            if output_file.exists():
                print(f"音频混合成功: {output_path}")
                print(f"输出文件大小: {output_file.stat().st_size / 1024:.1f} KB")
                return True
            else:
                print("音频混合失败：输出文件未生成")
                return False
            
        except subprocess.CalledProcessError as e:
            print(f"FFmpeg执行失败: {e}")
            print(f"错误输出: {e.stderr}")
            return False
        except Exception as e:
            print(f"音频混合失败: {str(e)}")
            return False

    def start_tts_generation_task(task_id, subtitles):
        """启动TTS音频生成任务的后台处理函数"""
        try:
            from videotrans import tts
            from videotrans.util import tools
            import subprocess
            
            print(f"开始TTS生成任务: {task_id}")
            
            # 创建任务目录
            task_dir = Path(TARGET_DIR) / task_id
            cache_dir = Path(config.TEMP_DIR) / task_id
            task_dir.mkdir(parents=True, exist_ok=True)
            cache_dir.mkdir(parents=True, exist_ok=True)
            
            # 准备TTS队列数据
            queue_tts = []
            for i, subtitle in enumerate(subtitles):
                if not subtitle.get('text', '').strip():
                    continue
                    
                # 计算时长
                start_time = int(subtitle.get('start_time', 0))
                end_time = int(subtitle.get('end_time', 0))
                duration = end_time - start_time
                
                if duration <= 0:
                    continue
                
                # 生成唯一文件名
                filename_md5 = tools.get_md5(
                    f"edgetts-{start_time}-{end_time}-zh-CN-XiaoxiaoNeural-+0%-+0%-+0Hz-{len(subtitle['text'])}-{i}")
                
                tts_item = {
                    "line": subtitle.get('line', i + 1),
                    "text": subtitle['text'],
                    "role": "zh-CN-XiaoxiaoNeural",  # 默认使用EdgeTTS中文女声
                    "start_time": start_time,
                    "end_time": end_time,
                    "startraw": subtitle.get('startraw', ''),
                    "endraw": subtitle.get('endraw', ''),
                    "rate": "+20%",  # 提高语速20%
                    "volume": "+0%",
                    "pitch": "+0Hz",
                    "tts_type": 0,  # EdgeTTS
                    "filename": config.TEMP_DIR + f"/dubbing_cache/{filename_md5}.wav"
                }
                queue_tts.append(tts_item)
            
            if not queue_tts:
                print("没有有效的字幕数据")
                return
            
            # 创建缓存目录
            Path(config.TEMP_DIR + "/dubbing_cache").mkdir(parents=True, exist_ok=True)
            
            print(f"开始生成TTS音频，共{len(queue_tts)}条字幕")
            
            # 设置TTS状态
            config.box_tts = 'ing'
            
            # 调用TTS引擎生成音频
            try:
                tts.run(queue_tts=queue_tts, language="zh-cn", 
                       inst=None, uuid=task_id, play=False, is_test=False)
                print("TTS引擎调用完成")
            except Exception as e:
                print(f"TTS引擎调用失败: {str(e)}")
                import traceback
                traceback.print_exc()
                config.box_tts = 'stop'
                return
            
            # 检查生成的音频文件
            audio_files = []
            print(f"检查音频文件，共{len(queue_tts)}个任务...")
            for i, item in enumerate(queue_tts):
                audio_path = Path(item['filename'])
                print(f"检查文件 {i+1}: {audio_path} - 存在: {audio_path.exists()}")
                if audio_path.exists():
                    audio_files.append({
                        'path': str(audio_path),
                        'start_time': item['start_time'],
                        'end_time': item['end_time'],
                        'text': item['text']
                    })
                    print(f"  ✓ 找到音频文件: {audio_path}")
                else:
                    print(f"  ✗ 音频文件不存在: {audio_path}")
            
            if not audio_files:
                print("没有生成任何音频文件")
                print("检查缓存目录中的文件:")
                cache_dir = Path(config.TEMP_DIR + "/dubbing_cache")
                if cache_dir.exists():
                        print(f"  缓存文件: {f}")
                else:
                    print("  缓存目录不存在")
                return
            
            print(f"成功生成{len(audio_files)}个音频片段")
            
            # 按时间顺序排序音频文件
            audio_files.sort(key=lambda x: x['start_time'])
            
            # 使用ffmpeg连接音频文件
            output_audio = task_dir / f"tts_audio_{int(time.time())}.wav"
            success = concatenate_audio_files(audio_files, str(output_audio))
            
            if success:
                print(f"TTS音频生成完成: {output_audio}")
                
                # 创建下载链接
                download_url = f'/{API_RESOURCE}/{task_id}/{output_audio.name}'
                
                # 保存任务结果信息
                result_info = {
                    "task_id": task_id,
                    "status": "completed",
                    "output_file": str(output_audio),
                    "download_url": download_url,
                    "audio_count": len(audio_files),
                    "total_duration": audio_files[-1]['end_time'] if audio_files else 0
                }
                
                # 保存结果到文件
                result_file = task_dir / "result.json"
                with open(result_file, 'w', encoding='utf-8') as f:
                    json.dump(result_info, f, ensure_ascii=False, indent=2)
                
            else:
                print("音频连接失败")
            
            # 重置TTS状态
            config.box_tts = 'stop'
                
        except Exception as e:
            print(f"TTS生成任务失败: {str(e)}")
            import traceback
            traceback.print_exc()

    def concatenate_audio_files(audio_files, output_path):
        """按照SRT时间轴精确连接音频文件"""
        try:
            import subprocess
            from pathlib import Path as _Path
            
            if not audio_files:
                print("没有音频文件需要连接")
                return False
            
            # 创建临时目录
            temp_dir = Path(output_path).parent / "temp_audio"
            temp_dir.mkdir(exist_ok=True)
            
            print(f"开始连接音频，共{len(audio_files)}个片段")
            
            # 计算总时长
            total_duration_ms = audio_files[-1]['end_time']
            total_duration_sec = total_duration_ms / 1000.0
            
            print(f"总时长: {total_duration_sec:.2f}秒")
            
            # 为每个音频片段添加静音前缀，确保时间对齐
            processed_files = []
            for i, audio_file in enumerate(audio_files):
                start_sec = audio_file['start_time'] / 1000.0
                end_sec = audio_file['end_time'] / 1000.0
                duration_sec = max(0.01, end_sec - start_sec)
                
                print(f"处理片段 {i+1}: {start_sec:.2f}s - {end_sec:.2f}s (时长: {duration_sec:.2f}s)")
                
                processed_file = temp_dir / f"processed_{i:04d}.wav"
                # 在拼接前，先将片段本体强制拉伸/压缩到目标时长，并提升音量
                try:
                    adj_path = _Path(audio_file['path'])
                    target_ms = int(round(duration_sec * 1000))
                    adjust_audio_length_and_volume(adj_path, target_ms, volume_boost=1.8)
                except Exception as _e:
                    print(f"  ⚠️ 片段时长调整失败，使用原片段: {audio_file['path']} -> {_e}")
                
                # 计算需要添加的静音时长
                if i == 0:
                    # 第一个文件，添加开始静音
                    silence_duration = start_sec
                else:
                    # 后续文件，添加与前一个文件的间隔
                    prev_end = audio_files[i-1]['end_time'] / 1000.0
                    silence_duration = start_sec - prev_end
                
                print(f"  静音时长: {silence_duration:.2f}秒")
                
                if silence_duration > 0:
                    # 添加静音前缀
                    cmd = [
                        'ffmpeg', '-y',
                        '-f', 'lavfi', '-i', 'anullsrc',
                        '-t', str(silence_duration),
                        '-i', audio_file['path'],
                        '-filter_complex', '[0][1]concat=n=2:v=0:a=1[out]',
                        '-map', '[out]',
                        '-ar', '44100',
                        '-ac', '2',
                        str(processed_file)
                    ]
                else:
                    # 直接复制文件
                    cmd = [
                        'ffmpeg', '-y',
                        '-i', audio_file['path'],
                        '-ar', '44100',
                        '-ac', '2',
                        str(processed_file)
                    ]
                
                print(f"  执行命令: {' '.join(cmd)}")
                result = subprocess.run(cmd, check=True, capture_output=True, text=True)
                
                if result.returncode == 0:
                    processed_files.append(str(processed_file))
                    print(f"  ✅ 处理成功")
                else:
                    print(f"  ❌ 处理失败: {result.stderr}")
                    return False
            
            # 连接所有处理后的音频文件
            if len(processed_files) == 1:
                # 只有一个文件，直接复制
                cmd = ['ffmpeg', '-y', '-i', processed_files[0], str(output_path)]
            else:
                # 多个文件，使用concat filter连接
                concat_filter = f'concat=n={len(processed_files)}:v=0:a=1[out]'
                cmd = ['ffmpeg', '-y']
                for file_path in processed_files:
                    cmd.extend(['-i', file_path])
                cmd.extend(['-filter_complex', concat_filter, '-map', '[out]', str(output_path)])
            
            print(f"连接音频文件: {' '.join(cmd)}")
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            
            if result.returncode == 0:
                print(f"✅ 音频连接完成: {output_path}")
                
                # 清理临时文件
                import shutil
                shutil.rmtree(temp_dir)
                return True
            else:
                print(f"❌ 音频连接失败: {result.stderr}")
                return False
            
        except Exception as e:
            print(f"音频连接失败: {str(e)}")
            import traceback
            traceback.print_exc()
            return False

    def start_voice_dubbing_task(task_id, video_path, srt_path, subtitles):
        """启动智能配音任务的后台处理函数"""
        try:
            from videotrans.task._dubbing import DubbingSrt
            from videotrans import tts
            from videotrans.util import tools
            
            # 分析说话人，为每个说话人分配不同的音色
            speakers = list(set([s.get('speaker', '') for s in subtitles if s.get('speaker')]))
            speaker_roles = {}
            
            # 为每个说话人分配EdgeTTS音色
            edgetts_roles = ['zh-CN-XiaoxiaoNeural', 'zh-CN-YunxiNeural', 'zh-CN-YunyangNeural', 'zh-CN-XiaochenNeural']
            for i, speaker in enumerate(speakers):
                if i < len(edgetts_roles):
                    speaker_roles[speaker] = edgetts_roles[i]
                else:
                    # 如果说话人太多，循环使用音色
                    speaker_roles[speaker] = edgetts_roles[i % len(edgetts_roles)]
            
            # 设置全局配置
            config.dubbing_role = {}
            for subtitle in subtitles:
                speaker = subtitle.get('speaker', '')
                if speaker in speaker_roles:
                    config.dubbing_role[subtitle.get('line', 1)] = speaker_roles[speaker]
            
            # 创建任务目录
            task_dir = Path(TARGET_DIR) / task_id
            cache_dir = Path(config.TEMP_DIR) / task_id
            task_dir.mkdir(parents=True, exist_ok=True)
            cache_dir.mkdir(parents=True, exist_ok=True)
            
            # 1. 从视频中提取音频
            audio_path = cache_dir / "extracted_audio.wav"
            tools.conver_to_16k(video_path, str(audio_path))
            
            # 2. 人声分离 - 使用Demucs分离人声和背景音乐
            bgm_path = cache_dir / "background.wav"  # 背景音乐
            vocal_path = cache_dir / "vocal.wav"     # 原人声
            
            try:
                print(f"开始人声分离（使用Demucs）...")
                success = separate_voice_background_demucs(str(audio_path), str(cache_dir))
                
                if success and bgm_path.exists():
                    print("Demucs人声分离成功")
                else:
                    print("Demucs人声分离失败，使用原音频作为背景音乐")
                    bgm_path = audio_path
                    
            except Exception as e:
                print(f"人声分离失败: {str(e)}")
                print("使用原音频作为背景音乐（无分离）")
                bgm_path = audio_path
            
            # 3. 创建配音任务配置
            obj = tools.format_video(video_path, None)
            obj['target_dir'] = str(task_dir)
            obj['cache_folder'] = str(cache_dir)
            
            cfg = {
                "name": srt_path,  # 使用SRT文件路径，不是视频路径
                "voice_role": "zh-CN-XiaoxiaoNeural",  # 默认角色
                "target_language_code": "zh-cn",
                "tts_type": tts.EDGE_TTS,  # 使用EdgeTTS
                "voice_rate": "+0%",
                "volume": "+0%",
                "pitch": "+0Hz",
                "out_ext": "wav",
                "voice_autorate": True,
                "is_multi_role": True,  # 启用多角色模式
                "bgm_path": str(bgm_path),  # 背景音乐路径
                "original_video": video_path,  # 原始视频路径
            }
            cfg.update(obj)
            
            # 4. 启动配音任务
            config.box_tts = 'ing'
            
            # 确保SRT文件可以被正确读取
            srt_file = Path(srt_path)
            try:
                # 测试读取SRT文件
                with open(srt_file, 'r', encoding='utf-8') as f:
                    test_read = f.read()
                print(f"SRT文件读取测试成功，长度: {len(test_read)}")
            except UnicodeDecodeError:
                print("UTF-8读取失败，尝试其他编码")
                # 尝试用其他编码重新保存
                try:
                    with open(srt_file, 'r', encoding='gbk') as f:
                        content = f.read()
                    srt_file.write_text(content, encoding='utf-8', errors='replace')
                    print("已重新保存为UTF-8编码")
                except:
                    # 如果GBK也失败，直接使用错误替换模式
                    with open(srt_file, 'r', encoding='utf-8', errors='replace') as f:
                        content = f.read()
                    srt_file.write_text(content, encoding='utf-8', errors='replace')
                    print("已使用错误替换模式重新保存")
            except Exception as e:
                print(f"SRT文件读取测试失败: {str(e)}")
                # 最后尝试，使用错误替换模式
                try:
                    with open(srt_file, 'r', encoding='utf-8', errors='replace') as f:
                        content = f.read()
                    srt_file.write_text(content, encoding='utf-8', errors='replace')
                    print("已使用错误替换模式重新保存")
                except:
                    print("无法修复SRT文件编码问题")
            
            # 创建一个安全的DubbingSrt子类来处理编码问题
            class SafeDubbingSrt(DubbingSrt):
                def dubbing(self):
                    try:
                        # 安全读取SRT文件
                        srt_path = Path(self.cfg['target_sub'])
                        srt_content = self._safe_read_srt(srt_path)
                        self._signal(text=srt_content, type="replace")
                        self._tts()
                    except Exception as e:
                        self.hasend = True
                        tools.send_notification(str(e), f'{self.cfg["basename"]}')
                        raise
                
                def _safe_read_srt(self, srt_path):
                    """安全读取SRT文件，处理编码问题"""
                    encodings = ['utf-8', 'gbk', 'gb2312', 'utf-16', 'latin-1']
                    
                    for encoding in encodings:
                        try:
                            with open(srt_path, 'r', encoding=encoding) as f:
                                content = f.read()
                            print(f"成功使用 {encoding} 编码读取SRT文件")
                            return content
                        except UnicodeDecodeError:
                            continue
                        except Exception as e:
                            print(f"使用 {encoding} 编码读取失败: {str(e)}")
                            continue
                    
                    # 如果所有编码都失败，使用错误替换模式
                    try:
                        with open(srt_path, 'r', encoding='utf-8', errors='replace') as f:
                            content = f.read()
                        print("使用UTF-8错误替换模式读取SRT文件")
                        return content
                    except Exception as e:
                        print(f"所有编码方式都失败: {str(e)}")
                        return ""
                
                def _tts(self):
                    """重写_tts方法，直接使用前端提供的字幕数据"""
                    queue_tts = []
                    # 获取字幕
                    try:
                        rate = int(str(self.cfg['voice_rate']).replace('%', ''))
                    except:
                        rate = 0
                    if rate >= 0:
                        rate = f"+{rate}%"
                    else:
                        rate = f"{rate}%"
                    
                    # 直接使用前端提供的字幕数据，而不是从SRT文件解析
                    subs = subtitles  # 使用传入的字幕数据
                    
                    # 取出每一条字幕，行号\n开始时间 --> 结束时间\n内容
                    for i, it in enumerate(subs):
                        if it.get('end_time', 0) <= it.get('start_time', 0):
                            continue
                        try:
                            spec_role = config.dubbing_role.get(int(it.get('line', 1))) if self.is_multi_role else None
                        except:
                            spec_role = None
                        voice_role = spec_role if spec_role else self.cfg['voice_role']

                        # 要保存到的文件
                        filename_md5 = tools.get_md5(
                            f"{self.cfg['tts_type']}-{it['start_time']}-{it['end_time']}-{voice_role}-{rate}-{self.cfg['volume']}-{self.cfg['pitch']}-{len(it['text'])}-{i}")
                        tmp_dict = {
                            "line": it['line'],
                            "text": it['text'],
                            "role": voice_role,
                            "start_time": it['start_time'],
                            "end_time": it['end_time'],
                            "rate": rate,
                            "startraw": it.get('startraw', ''),
                            "endraw": it.get('endraw', ''),
                            "volume": self.cfg['volume'],
                            "pitch": self.cfg['pitch'],
                            "tts_type": int(self.cfg['tts_type']),
                            "filename": config.TEMP_DIR + f"/dubbing_cache/{filename_md5}.wav"}
                        queue_tts.append(tmp_dict)
                    
                    Path(config.TEMP_DIR + "/dubbing_cache").mkdir(parents=True, exist_ok=True)
                    if len(queue_tts) < 1:
                        return
                    
                    # 调用TTS引擎
                    tts.run(queue_tts=queue_tts, language=self.cfg['target_language_code'], 
                           inst=self, uuid=self.uuid, play=False, is_test=False)
            
            trk = SafeDubbingSrt(cfg=cfg)
            trk.dubbing()
            
            # 5. 合成最终视频（配音 + 背景音乐 + 原视频画面）
            final_video_path = task_dir / f"dubbed_{Path(video_path).stem}.mp4"
            
            # 检查是否有背景音乐分离
            if bgm_path == audio_path:
                # 没有背景音乐分离，直接使用配音音频
                print("无背景音乐分离，直接使用配音音频")
                combine_audio_with_video_simple(
                    str(trk.cfg['target_wav']),  # 配音音频
                    video_path,  # 原视频
                    str(final_video_path)  # 输出视频
                )
            else:
                # 有背景音乐分离，混合背景音乐和配音
                print("混合背景音乐和配音")
                combine_audio_with_video(
                    str(bgm_path),  # 背景音乐
                    str(trk.cfg['target_wav']),  # 配音音频
                    video_path,  # 原视频
                    str(final_video_path)  # 输出视频
                )
            
            print(f"配音任务完成: {final_video_path}")
            
        except Exception as e:
            print(f"配音任务失败: {str(e)}")
            import traceback
            traceback.print_exc()

    def separate_voice_background_demucs(audio_path, output_dir):
        """使用Demucs分离人声和背景音乐"""
        try:
            import subprocess
            import shutil
            from pathlib import Path
            
            output_path = Path(output_dir)
            vocal_path = output_path / "vocal.wav"
            background_path = output_path / "background.wav"
            
            print(f"开始Demucs人声分离...")
            print(f"输入音频: {audio_path}")
            print(f"输出目录: {output_dir}")
            
            # 检查输入文件
            if not Path(audio_path).exists():
                print(f"输入音频文件不存在: {audio_path}")
                return False
            
            # 尝试多种方式调用Demucs
            demucs_commands = [
                ['demucs'],
                ['python', '-m', 'demucs'],
                ['python3', '-m', 'demucs'],
                ['python3.11', '-m', 'demucs']
            ]
            
            demucs_cmd = None
            for cmd in demucs_commands:
                try:
                    result = subprocess.run(cmd + ['--help'], capture_output=True, text=True)
                    if result.returncode == 0:
                        demucs_cmd = cmd
                        print(f"找到Demucs: {' '.join(cmd)}")
                        break
                except FileNotFoundError:
                    continue
            
            if not demucs_cmd:
                print("无法找到Demucs，请安装: pip install demucs")
                return False
            
            # 使用Demucs分离 - 使用更简单的参数
            print("执行Demucs分离...")
            demucs_args = [
                *demucs_cmd,
                '--two-stems', 'vocals',  # 分离人声和背景
                '--out', str(output_path),
                str(audio_path)
            ]
            
            print(f"执行命令: {' '.join(demucs_args)}")
            result = subprocess.run(demucs_args, capture_output=True, text=True, timeout=300)
            
            print(f"Demucs返回码: {result.returncode}")
            if result.stdout:
                print(f"Demucs输出: {result.stdout}")
            if result.stderr:
                print(f"Demucs错误: {result.stderr}")
            
            if result.returncode != 0:
                print(f"Demucs分离失败，返回码: {result.returncode}")
                return False
            
            # Demucs输出目录结构 - 检查多种可能的输出结构
            audio_name = Path(audio_path).stem
            possible_output_dirs = [
                output_path / "htdemucs" / audio_name,
                output_path / "htdemucs",
                output_path / audio_name,
                output_path
            ]
            
            vocals_file = None
            no_vocals_file = None
            
            for demucs_output_dir in possible_output_dirs:
                if demucs_output_dir.exists():
                    print(f"检查输出目录: {demucs_output_dir}")
                    
                    # 查找分离后的文件
                    vocals_candidate = demucs_output_dir / "vocals.wav"
                    no_vocals_candidate = demucs_output_dir / "no_vocals.wav"
                    
                    if vocals_candidate.exists() and no_vocals_candidate.exists():
                        vocals_file = vocals_candidate
                        no_vocals_file = no_vocals_candidate
                        print(f"找到分离文件: {vocals_file}, {no_vocals_file}")
                        break
                    else:
                        # 列出目录内容用于调试
                        print(f"目录内容: {list(demucs_output_dir.iterdir())}")
            
            if vocals_file and no_vocals_file:
                # 复制到指定位置
                shutil.copy2(vocals_file, vocal_path)
                shutil.copy2(no_vocals_file, background_path)
                
                print(f"人声分离成功: {vocal_path} (大小: {vocal_path.stat().st_size / 1024:.1f} KB)")
                print(f"背景音分离成功: {background_path} (大小: {background_path.stat().st_size / 1024:.1f} KB)")
                
                # 清理Demucs临时文件
                for demucs_output_dir in possible_output_dirs:
                    if demucs_output_dir.exists() and demucs_output_dir != output_path:
                        try:
                            shutil.rmtree(demucs_output_dir)
                            print(f"清理临时目录: {demucs_output_dir}")
                        except:
                            pass
                
                return True
            else:
                print("Demucs输出文件未找到")
                print(f"检查的路径: {possible_output_dirs}")
                return False
                
        except subprocess.TimeoutExpired:
            print("Demucs分离超时")
            return False
        except Exception as e:
            print(f"Demucs分离异常: {str(e)}")
            import traceback
            traceback.print_exc()
            return False

    def combine_audio_with_video(bgm_path, dubbing_path, video_path, output_path):
        """将背景音乐、配音和原视频画面合成最终视频"""
        try:
            import subprocess
            
            # 使用ffmpeg合成音频和视频
            # 1. 将背景音乐和配音混合
            mixed_audio = Path(output_path).parent / "mixed_audio.wav"
            cmd1 = [
                'ffmpeg', '-y',
                '-i', bgm_path,
                '-i', dubbing_path,
                '-filter_complex',
                '[0:a]volume=0.5[bgm];[1:a]volume=1.4[tts];' \
                '[bgm][tts]amix=inputs=2:duration=longest:normalize=0[mixed]',
                '-map', '[mixed]',
                '-c:a', 'aac',
                '-b:a', '128k',
                str(mixed_audio)
            ]
            subprocess.run(cmd1, check=True, capture_output=True)
            
            # 2. 将混合音频与原视频画面合成
            cmd2 = [
                'ffmpeg', '-y',
                '-i', video_path,
                '-i', str(mixed_audio),
                '-c:v', 'copy',  # 复制视频流，不重新编码
                '-c:a', 'aac',
                '-b:a', '128k',
                '-map', '0:v:0',  # 使用原视频的画面
                '-map', '1:a:0',  # 使用混合后的音频
                '-shortest',  # 以较短的流为准
                str(output_path)
            ]
            subprocess.run(cmd2, check=True, capture_output=True)
            
            # 清理临时文件
            if mixed_audio.exists():
                mixed_audio.unlink()
                
        except Exception as e:
            print(f"视频合成失败: {str(e)}")
            # 如果合成失败，至少保留配音音频文件

    def combine_audio_with_video_simple(dubbing_path, video_path, output_path):
        """将配音音频与原视频画面合成（无背景音乐混合）"""
        try:
            import subprocess
            
            # 检查输入文件
            dubbing_file = Path(dubbing_path)
            video_file = Path(video_path)
            
            if not dubbing_file.exists():
                print(f"配音音频文件不存在: {dubbing_path}")
                return False
                
            if not video_file.exists():
                print(f"视频文件不存在: {video_path}")
                return False
            
            print(f"配音音频文件大小: {dubbing_file.stat().st_size / 1024:.1f} KB")
            print(f"视频文件大小: {video_file.stat().st_size / 1024:.1f} KB")
            
            # 直接将配音音频与原视频画面合成
            cmd = [
                'ffmpeg', '-y',
                '-i', str(video_path),
                '-i', str(dubbing_path),
                '-c:v', 'copy',  # 复制视频流，不重新编码
                '-c:a', 'aac',
                '-b:a', '128k',
                '-map', '0:v:0',  # 使用原视频的画面
                '-map', '1:a:0',  # 使用配音音频
                '-shortest',  # 以较短的流为准
                str(output_path)
            ]
            
            print(f"执行FFmpeg视频合成命令: {' '.join(cmd)}")
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            
            output_file = Path(output_path)
            if output_file.exists():
                print(f"视频合成完成: {output_path}")
                print(f"输出视频文件大小: {output_file.stat().st_size / 1024:.1f} KB")
                return True
            else:
                print("视频合成失败：输出文件未生成")
                return False
                
        except subprocess.CalledProcessError as e:
            print(f"FFmpeg视频合成执行失败: {e}")
            print(f"错误输出: {e.stderr}")
            return False
        except Exception as e:
            print(f"视频合成失败: {str(e)}")
            return False

    @app.route('/synthesis_result/<task_id>')
    def synthesis_result(task_id):
        """视频合成结果页面"""
        task_dir = Path(TARGET_DIR) / task_id
        if not task_dir.exists():
            return "任务不存在", 404
        
        # 查找输出文件
        files = [f for f in task_dir.iterdir() if f.is_file()]
        output_files = []
        for f in files:
            if f.suffix.lower() in ['.mp4', '.avi', '.mov', '.mkv', '.wav', '.mp3', '.m4a']:
                output_files.append(f)
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>视频合成结果 - {task_id}</title>
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 20px; background: #f5f5f5; }}
                .container {{ max-width: 800px; margin: 0 auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
                h1 {{ color: #333; margin-bottom: 20px; }}
                .file-list {{ list-style: none; padding: 0; }}
                .file-item {{ padding: 10px; border: 1px solid #ddd; margin: 10px 0; border-radius: 4px; background: #f9f9f9; }}
                .file-item a {{ color: #007AFF; text-decoration: none; font-weight: bold; }}
                .file-item a:hover {{ text-decoration: underline; }}
                .status {{ padding: 10px; border-radius: 4px; margin: 10px 0; }}
                .status.processing {{ background: #fff3cd; border: 1px solid #ffeaa7; color: #856404; }}
                .status.completed {{ background: #d4edda; border: 1px solid #c3e6cb; color: #155724; }}
                .status.error {{ background: #f8d7da; border: 1px solid #f5c6cb; color: #721c24; }}
                .video-player {{ width: 100%; margin: 10px 0; }}
                .audio-player {{ width: 100%; margin: 10px 0; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>视频合成结果</h1>
                <div class="task-info">
                    <p><strong>任务ID:</strong> {task_id}</p>
                    <p><strong>状态:</strong> <span id="status">检查中...</span></p>
                </div>
                <div id="fileList">
                    <p>正在检查输出文件...</p>
                </div>
            </div>
            
            <script>
                const taskId = '{task_id}';
                
                async function checkStatus() {{
                    try {{
                        const res = await fetch(`/task_status?task_id=${{taskId}}`);
                        const data = await res.json();
                        
                        if (data.code === 0) {{
                            document.getElementById('status').textContent = '已完成';
                            document.getElementById('status').className = 'status completed';
                            
                            const files = data.data?.url || [];
                            const fileList = document.getElementById('fileList');
                            if (files.length > 0) {{
                                fileList.innerHTML = '<h3>输出文件:</h3><ul class="file-list">';
                                files.forEach(url => {{
                                    const fileName = url.split('/').pop();
                                    const isVideo = fileName.match(/\\.(mp4|avi|mov|mkv)$/i);
                                    const isAudio = fileName.match(/\\.(wav|mp3|m4a)$/i);
                                    fileList.innerHTML += `<li class="file-item">
                                        <a href="${{url}}" target="_blank">${{fileName}}</a>
                                        ${{isVideo ? '<br><video controls class="video-player"><source src="' + url + '" type="video/mp4">您的浏览器不支持视频播放</video>' : ''}}
                                        ${{isAudio ? '<br><audio controls class="audio-player"><source src="' + url + '" type="audio/wav">您的浏览器不支持音频播放</audio>' : ''}}
                                    </li>`;
                                }});
                                fileList.innerHTML += '</ul>';
                            }} else {{
                                fileList.innerHTML = '<p>暂无输出文件</p>';
                            }}
                        }} else if (data.code === -1) {{
                            document.getElementById('status').textContent = data.msg || '处理中...';
                            document.getElementById('status').className = 'status processing';
                            setTimeout(checkStatus, 2000);
                        }} else {{
                            document.getElementById('status').textContent = data.msg || '处理失败';
                            document.getElementById('status').className = 'status error';
                        }}
                    }} catch (e) {{
                        document.getElementById('status').textContent = '检查状态失败';
                        document.getElementById('status').className = 'status error';
                    }}
                }}
                
                checkStatus();
            </script>
        </body>
        </html>
        """
        return html

    @app.route('/tts_result/<task_id>')
    def tts_result(task_id):
        """TTS结果页面"""
        task_dir = Path(TARGET_DIR) / task_id
        if not task_dir.exists():
            return "任务不存在", 404
        
        # 查找输出文件
        files = [f for f in task_dir.iterdir() if f.is_file()]
        output_files = []
        for f in files:
            if f.suffix.lower() in ['.wav', '.mp3', '.m4a']:
                output_files.append(f)
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>TTS结果 - {task_id}</title>
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 20px; background: #f5f5f5; }}
                .container {{ max-width: 800px; margin: 0 auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
                h1 {{ color: #333; margin-bottom: 20px; }}
                .file-list {{ list-style: none; padding: 0; }}
                .file-item {{ padding: 10px; border: 1px solid #ddd; margin: 10px 0; border-radius: 4px; background: #f9f9f9; }}
                .file-item a {{ color: #007AFF; text-decoration: none; font-weight: bold; }}
                .file-item a:hover {{ text-decoration: underline; }}
                .status {{ padding: 10px; border-radius: 4px; margin: 10px 0; }}
                .status.processing {{ background: #fff3cd; border: 1px solid #ffeaa7; color: #856404; }}
                .status.completed {{ background: #d4edda; border: 1px solid #c3e6cb; color: #155724; }}
                .status.error {{ background: #f8d7da; border: 1px solid #f5c6cb; color: #721c24; }}
                .audio-player {{ width: 100%; margin: 10px 0; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>TTS音频生成结果</h1>
                <div class="task-info">
                    <p><strong>任务ID:</strong> {task_id}</p>
                    <p><strong>状态:</strong> <span id="status">检查中...</span></p>
                </div>
                <div id="fileList">
                    <p>正在检查输出文件...</p>
                </div>
            </div>
            
            <script>
                const taskId = '{task_id}';
                
                async function checkStatus() {{
                    try {{
                        const res = await fetch(`/task_status?task_id=${{taskId}}`);
                        const data = await res.json();
                        
                        if (data.code === 0) {{
                            document.getElementById('status').textContent = '已完成';
                            document.getElementById('status').className = 'status completed';
                            
                            const files = data.data?.url || [];
                            const fileList = document.getElementById('fileList');
                            if (files.length > 0) {{
                                fileList.innerHTML = '<h3>输出文件:</h3><ul class="file-list">';
                                files.forEach(url => {{
                                    const fileName = url.split('/').pop();
                                    const isAudio = fileName.match(/\\.(wav|mp3|m4a)$/i);
                                    fileList.innerHTML += `<li class="file-item">
                                        <a href="${{url}}" target="_blank">${{fileName}}</a>
                                        ${{isAudio ? '<br><audio controls class="audio-player"><source src="' + url + '" type="audio/wav">您的浏览器不支持音频播放</audio>' : ''}}
                                    </li>`;
                                }});
                                fileList.innerHTML += '</ul>';
                            }} else {{
                                fileList.innerHTML = '<p>暂无输出文件</p>';
                            }}
                        }} else if (data.code === -1) {{
                            document.getElementById('status').textContent = data.msg || '处理中...';
                            document.getElementById('status').className = 'status processing';
                            setTimeout(checkStatus, 2000);
                        }} else {{
                            document.getElementById('status').textContent = data.msg || '处理失败';
                            document.getElementById('status').className = 'status error';
                        }}
                    }} catch (e) {{
                        document.getElementById('status').textContent = '检查状态失败';
                        document.getElementById('status').className = 'status error';
                    }}
                }}
                
                checkStatus();
            </script>
        </body>
        </html>
        """
        return html

    @app.route('/dubbing_result/<task_id>')
    def dubbing_result(task_id):
        """配音结果页面"""
        task_dir = Path(TARGET_DIR) / task_id
        if not task_dir.exists():
            return "任务不存在", 404
        
        # 查找输出文件
        files = [f for f in task_dir.iterdir() if f.is_file()]
        output_files = []
        for f in files:
            if f.suffix.lower() in ['.wav', '.mp3', '.m4a', '.mp4']:
                output_files.append(f)
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>配音结果 - {task_id}</title>
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 20px; background: #f5f5f5; }}
                .container {{ max-width: 800px; margin: 0 auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
                h1 {{ color: #333; margin-bottom: 20px; }}
                .file-list {{ list-style: none; padding: 0; }}
                .file-item {{ padding: 10px; border: 1px solid #ddd; margin: 10px 0; border-radius: 4px; background: #f9f9f9; }}
                .file-item a {{ color: #007AFF; text-decoration: none; font-weight: bold; }}
                .file-item a:hover {{ text-decoration: underline; }}
                .status {{ padding: 10px; border-radius: 4px; margin: 10px 0; }}
                .status.processing {{ background: #fff3cd; border: 1px solid #ffeaa7; color: #856404; }}
                .status.completed {{ background: #d4edda; border: 1px solid #c3e6cb; color: #155724; }}
                .status.error {{ background: #f8d7da; border: 1px solid #f5c6cb; color: #721c24; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>配音结果</h1>
                <div class="task-info">
                    <p><strong>任务ID:</strong> {task_id}</p>
                    <p><strong>状态:</strong> <span id="status">检查中...</span></p>
                </div>
                <div id="fileList">
                    <p>正在检查输出文件...</p>
                </div>
            </div>
            
            <script>
                const taskId = '{task_id}';
                
                async function checkStatus() {{
                    try {{
                        const res = await fetch(`/task_status?task_id=${{taskId}}`);
                        const data = await res.json();
                        
                        if (data.code === 0) {{
                            document.getElementById('status').textContent = '已完成';
                            document.getElementById('status').className = 'status completed';
                            
                            const files = data.data?.url || [];
                            const fileList = document.getElementById('fileList');
                            if (files.length > 0) {{
                                fileList.innerHTML = '<h3>输出文件:</h3><ul class="file-list">';
                                files.forEach(url => {{
                                    const fileName = url.split('/').pop();
                                    fileList.innerHTML += `<li class="file-item"><a href="${{url}}" target="_blank">${{fileName}}</a></li>`;
                                }});
                                fileList.innerHTML += '</ul>';
                            }} else {{
                                fileList.innerHTML = '<p>暂无输出文件</p>';
                            }}
                        }} else if (data.code === -1) {{
                            document.getElementById('status').textContent = data.msg || '处理中...';
                            document.getElementById('status').className = 'status processing';
                            setTimeout(checkStatus, 2000);
                        }} else {{
                            document.getElementById('status').textContent = data.msg || '处理失败';
                            document.getElementById('status').className = 'status error';
                        }}
                    }} catch (e) {{
                        document.getElementById('status').textContent = '检查状态失败';
                        document.getElementById('status').className = 'status error';
                    }}
                }}
                
                checkStatus();
            </script>
        </body>
        </html>
        """
        return html


    # 获取任务进度
    """
    根据任务id，获取当前任务的状态
    
    请求数据类型：优先GET中获取，不存在则从POST中获取，都不存在则从 json数据中获取
    
    请求参数: 
    task_id:必须，字符串类型
    
    返回:json格式数据
    code:-1=进行中，0=成功结束，>0=出错了
    msg:code为-1时为进度信息，code>0时为出错信息，成功时为ok
    data:仅当code==0成功时存在，是一个dict对象
        absolute_path是生成的文件列表list，每项均是一个文件的绝对路径
        url 是生成的文件列表list，每项均是一个可访问的url
    
    
    失败：{"code":1,"msg":"不存在该任务"}
    进行中：{"code":-1,"msg":"正在合成声音"} 
    成功: {"code":0,"msg":"ok","data":{"absolute_path":["/data/1.srt","/data/1.mp4"],"url":["http://127.0.0.1:9011/task_id/1.srt"]}}
    
    
    示例
    def test_task_status():
        res=requests.post("http://127.0.0.1:9011/task_status",json={
            "task_id":"06c238d250f0b51248563c405f1d7294"
        })
        print(res.json())
    
    {
      "code": 0,
      "data": {
        "absolute_path": [
          "F:/python/pyvideo/apidata/daa33fee2537b47a0b12e12b926a4b01/10ass.mp4",
          "F:/python/pyvideo/apidata/daa33fee2537b47a0b12e12b926a4b01/en.m4a",
          "F:/python/pyvideo/apidata/daa33fee2537b47a0b12e12b926a4b01/en.srt",
          "F:/python/pyvideo/apidata/daa33fee2537b47a0b12e12b926a4b01/end.srt.ass",
          "F:/python/pyvideo/apidata/daa33fee2537b47a0b12e12b926a4b01/zh-cn.m4a",
          "F:/python/pyvideo/apidata/daa33fee2537b47a0b12e12b926a4b01/zh-cn.srt",
          "F:/python/pyvideo/apidata/daa33fee2537b47a0b12e12b926a4b01/文件说明.txt"
        ],
        "url": [
          "http://127.0.0.1:9011/apidata/daa33fee2537b47a0b12e12b926a4b01/10ass.mp4",
          "http://127.0.0.1:9011/apidata/daa33fee2537b47a0b12e12b926a4b01/en.m4a",
          "http://127.0.0.1:9011/apidata/daa33fee2537b47a0b12e12b926a4b01/en.srt",
          "http://127.0.0.1:9011/apidata/daa33fee2537b47a0b12e12b926a4b01/end.srt.ass",
          "http://127.0.0.1:9011/apidata/daa33fee2537b47a0b12e12b926a4b01/zh-cn.m4a",
          "http://127.0.0.1:9011/apidata/daa33fee2537b47a0b12e12b926a4b01/zh-cn.srt",
          "http://127.0.0.1:9011/apidata/daa33fee2537b47a0b12e12b926a4b01/文件说明.txt"
        ]
      },
      "msg": "ok"
    }
    
    """
    @app.route('/task_status', methods=['POST', 'GET'])
    def task_status():
        # 1. 优先从 GET 请求参数中获取 task_id
        task_id = request.args.get('task_id')

        # 2. 如果 GET 参数中没有 task_id，再从 POST 表单中获取
        if task_id is None:
            task_id = request.form.get('task_id')

        # 3. 如果 POST 表单中也没有 task_id，再从 JSON 请求体中获取
        if task_id is None and request.is_json:
            task_id = request.json.get('task_id')
        if not task_id:
            return jsonify({"code": 1, "msg": "The parem  task_id is not set"})
        return _get_task_data(task_id)
        

    
    # 获取多个任务 前台 content-type:application/json, 数据 {task_id_list:[id1,id2,....]}
    @app.route('/task_status_list', methods=['POST', 'GET'])
    def task_status_list():
        # 1. 优先从 GET 请求参数中获取 task_id
        task_ids= request.json.get('task_id_list',[])
        if not task_ids or len(task_ids)<1:
            return jsonify({"code": 1, "msg": "缺少任务id"})
        
        return_data={}
        for task_id in task_ids:
            return_data[task_id]=_get_task_data(task_id)
        return jsonify({"code": 0, "msg": "ok","data":return_data})
    
    def _get_task_data(task_id):
        file = PROCESS_INFO + f'/{task_id}.json'
        if not Path(file).is_file():
            if task_id in config.uuid_logs_queue:
                return {"code": -1, "msg": _get_order(task_id)}

            return {"code": 1, "msg": f"该任务 {task_id} 不存在"}

        try:
            data = json.loads(Path(file).read_text(encoding='utf-8'))
        except Exception as e:
            return {"code": -1, "msg": Path(file).read_text(encoding='utf-8')}

        if data['type'] == 'error':
            return {"code": 3, "msg": data["text"]}
        if data['type'] in logs_status_list:
            text=data.get('text','').strip()
            return {"code": -1, "msg": text if text else '等待处理中'}
        # 完成，输出所有文件
        file_list = _get_files_in_directory(f'{TARGET_DIR}/{task_id}')
        if len(file_list) < 1:
            return {"code": 4, "msg": '未生成任何结果文件，可能出错了'}

        return {
            "code": 0,
            "msg": "ok",
            "data": {
                "absolute_path": [f'{TARGET_DIR}/{task_id}/{name}' for name in file_list],
                "url": [f'{request.scheme}://{request.host}/{API_RESOURCE}/{task_id}/{name}' for name in file_list],
            }
        }

    # 排队
    def _get_order(task_id):
        order_num=0
        for it in config.prepare_queue:
            order_num+=1
            if it.uuid == task_id:
                return f'当前处于预处理队列第{order_num}位' if config.defaulelang=='zh' else f"No.{order_num} on perpare queue"
        
        order_num=0
        for it in config.regcon_queue:
            order_num+=1
            if it.uuid == task_id:
                return f'当前处于语音识别队列第{order_num}位' if config.defaulelang=='zh' else f"No.{order_num} on perpare queue"
        order_num=0
        for it in config.trans_queue:
            order_num+=1
            if it.uuid == task_id:
                return f'当前处于字幕翻译队列第{order_num}位' if config.defaulelang=='zh' else f"No.{order_num} on perpare queue"
        order_num=0
        for it in config.dubb_queue:
            order_num+=1
            if it.uuid == task_id:
                return f'当前处于配音队列第{order_num}位' if config.defaulelang=='zh' else f"No.{order_num} on perpare queue"
        order_num=0
        for it in config.align_queue:
            order_num+=1
            if it.uuid == task_id:
                return f'当前处于声画对齐队列第{order_num}位' if config.defaulelang=='zh' else f"No.{order_num} on perpare queue"
        order_num=0
        for it in config.assemb_queue:
            order_num+=1
            if it.uuid == task_id:
                return f'当前处于输出整理队列第{order_num}位' if config.defaulelang=='zh' else f"No.{order_num} on perpare queue"
        return '正在排队等待执行中，请稍后' if config.defaulelang=='zh' else f"Waiting in queue"
    
    def _get_files_in_directory(dirname):
        """
        使用 pathlib 库获取指定目录下的所有文件名，并返回一个文件名列表。

        参数:
        dirname (str): 要获取文件的目录路径

        返回:
        list: 包含目录中所有文件名的列表
        """
        try:
            # 使用 Path 对象获取目录中的所有文件
            path = Path(dirname)
            files = [f.name for f in path.iterdir() if f.is_file()]
            return files
        except Exception as e:
            print(f"Error while accessing directory {dirname}: {e}")
            return []


    def _listen_queue():
        # 监听队列日志 uuid_logs_queue 不在停止中的 stoped_uuid_set
        Path(TARGET_DIR + f'/processinfo').mkdir(parents=True, exist_ok=True)
        while 1:
            # 找出未停止的
            uuid_list = list(config.uuid_logs_queue.keys())
            uuid_list = [uuid for uuid in uuid_list if uuid not in config.stoped_uuid_set]
            # 全部结束
            if len(uuid_list) < 1:
                time.sleep(1)
                continue
            while len(uuid_list) > 0:
                uuid = uuid_list.pop(0)
                if uuid in config.stoped_uuid_set:
                    continue
                try:
                    q = config.uuid_logs_queue.get(uuid)
                    if not q:
                        continue
                    data = q.get(block=False)
                    if not data:
                        continue

                    if data['type'] not in end_status_list + logs_status_list:
                        continue
                    with open(PROCESS_INFO + f'/{uuid}.json', 'w', encoding='utf-8') as f:
                        f.write(json.dumps(data))
                    if data['type'] in end_status_list:
                        config.stoped_uuid_set.add(uuid)
                        del config.uuid_logs_queue[uuid]
                except Exception:
                    pass
            time.sleep(0.1)

    multiprocessing.freeze_support()  # Windows 上需要这个来避免子进程的递归执行问题
    print(f'Starting... API URL is   http://{HOST}:{PORT}')
    print(f'Document at https://pyvideotrans.com/api-cn')
    start_thread()
    threading.Thread(target=_listen_queue).start()
    try:
        print(f'\nAPI URL is   http://{HOST}:{PORT}')
        serve(app, host=HOST, port=int(PORT))
    except Exception as e:
        import traceback
        traceback.print_exc()
