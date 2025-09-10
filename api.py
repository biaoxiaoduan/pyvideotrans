if __name__ == '__main__':
    print('API ...')
    import json
    import multiprocessing
    import random
    import re
    import shutil
    import threading
    import time
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
                .list {{ border: 1px solid #e5e5e5; border-radius: 8px; overflow: auto; padding: 8px; }}
                .item {{ padding: 8px; border-radius: 6px; cursor: pointer; margin-bottom: 6px; }}
                .item:hover {{ background: #f7f7f7; }}
                .item.active {{ background: #e9f3ff; }}
                .time {{ color:#666; font-size:12px; }}
                .speakerSel {{ margin-left: 8px; padding: 2px 6px; font-size: 12px; }}
                .textEdit {{ width: 100%; box-sizing: border-box; resize: vertical; min-height: 38px; font-size: 14px; line-height: 1.4; padding: 6px 8px; border: 1px solid #ddd; border-radius: 6px; }}
                .player {{ display:flex; flex-direction: column; gap: 8px; }}
                .timeline-wrap {{ border:1px solid #e5e5e5; border-radius:8px; padding:8px; }}
                canvas {{ width: 100%; height: 120px; display:block; }}
            </style>
        </head>
        <body>
            <header>
                <h3 style=\"margin:0;\">字幕查看器</h3>
                <div class=\"task\">任务: {task_id}</div>
                <div style=\"margin-left:auto; display:flex; gap:8px; align-items:center;\">
                    <button id=\"btnVoiceDubbing\" style=\"padding:6px 12px; border:1px solid #007AFF; background:#007AFF; color:#fff; border-radius:6px; cursor:pointer;\">智能配音</button>
                    <button id=\"btnSaveSrt\" style=\"padding:6px 12px; border:1px solid #ddd; background:#fff; border-radius:6px; cursor:pointer;\">下载SRT(含spk)</button>
                    <button id="btnSaveJson" style="padding:6px 12px; border:1px solid #ddd; background:#fff; border-radius:6px; cursor:pointer;">下载JSON</button>
                </div>
            </header>
            <div class=\"container\">
                <div class=\"list\" id=\"subList\"></div>
                <div class=\"player\">
                    <video id=\"video\" src=\"((VIDEO_URL))\" controls crossorigin=\"anonymous\" style=\"width:100%;max-height:60vh;background:#000\"></video>
                    <div class=\"timeline-wrap\">
                        <canvas id=\"timeline\" width=\"1200\" height=\"120\"></canvas>
                    </div>
                </div>
            </div>

            <script>
            const taskId = ((TASK_ID_JSON));
            const listEl = document.getElementById('subList');
            const videoEl = document.getElementById('video');
            const canvas = document.getElementById('timeline');
            const ctx = canvas.getContext('2d');
            const btnSaveSrt = document.getElementById('btnSaveSrt');
            const btnSaveJson = document.getElementById('btnSaveJson');
            let cues = [];
            let videoMs = 0;
            let speakers = [];

            function fmtMs(ms) {
                const s = Math.floor(ms/1000); const hh = String(Math.floor(s/3600)).padStart(2,'0');
                const mm = String(Math.floor((s%3600)/60)).padStart(2,'0');
                const ss = String(s%60).padStart(2,'0');
                const mmm = String(ms%1000).padStart(3,'0');
                return `${hh}:${mm}:${ss},${mmm}`;
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
                    sel.addEventListener('change', () => { c.speaker = sel.value; });
                    const content = document.createElement('textarea');
                    content.className = 'textEdit';
                    content.value = c.text || '';
                    content.addEventListener('input', () => { c.text = content.value; });
                    tx.appendChild(sel); tx.appendChild(content);
                    item.appendChild(t); item.appendChild(tx);
                    item.addEventListener('click', () => {
                        videoEl.currentTime = (c.start || 0) / 1000;
                    });
                    listEl.appendChild(item);
                });
            }

            function drawTimeline(currentMs=0) {
                const w = canvas.clientWidth; const h = canvas.height;
                if (canvas.width !== w) canvas.width = w;
                ctx.clearRect(0,0,canvas.width,canvas.height);
                ctx.fillStyle = '#fafafa';
                ctx.fillRect(0,0,canvas.width,canvas.height);
                const pad = 8; const barH = 24; const top = (canvas.height - barH)/2;
                cues.forEach((c, i) => {
                    const x = Math.max(0, Math.floor((c.start / videoMs) * (canvas.width - 2*pad)) + pad);
                    const wbar = Math.max(2, Math.floor(((c.end - c.start) / videoMs) * (canvas.width - 2*pad)));
                    ctx.fillStyle = (currentMs>=c.start && currentMs<c.end) ? '#4e8cff' : '#cddffd';
                    ctx.fillRect(x, top, wbar, barH);
                });
                const xnow = Math.floor((currentMs / videoMs) * (canvas.width - 2*pad)) + pad;
                ctx.strokeStyle = '#ff3b30';
                ctx.beginPath();
                ctx.moveTo(xnow, 0); ctx.lineTo(xnow, canvas.height); ctx.stroke();
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

            canvas.addEventListener('click', (e) => {
                const rect = canvas.getBoundingClientRect();
                const x = e.clientX - rect.left; const pad = 8; const ratio = Math.min(1, Math.max(0, (x - pad) / (canvas.width - 2*pad)));
                const ms = ratio * videoMs; videoEl.currentTime = ms / 1000;
            });

            window.addEventListener('resize', () => drawTimeline(videoEl.currentTime*1000));

            videoEl.addEventListener('timeupdate', () => updateActive(Math.floor(videoEl.currentTime*1000)));

            fetch(`/viewer_api/${taskId}/subtitles`).then(r=>r.json()).then(data => {
                if (data && data.code === 0) {
                    cues = data.subtitles || [];
                    videoMs = data.video_ms || (cues.length? cues[cues.length-1].end : 0);
                    speakers = (data.speakers || []).filter(Boolean);
                    renderList();
                    drawTimeline(0);
                }
            });

            async function onSaveSrt() {
                try {
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
                        window.location.href = data.download_url;
                    } else {
                        alert(data && data.msg ? data.msg : '保存失败');
                    }
                } catch (e) {
                    console.error(e);
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

            async function onVoiceDubbing() {
                try {
                    if (!cues || cues.length === 0) {
                        alert('没有字幕数据，无法进行配音');
                        return;
                    }
                    
                    // 检查是否有说话人信息
                    const hasSpeakers = cues.some(c => c.speaker && c.speaker.trim());
                    if (!hasSpeakers) {
                        alert('字幕中没有说话人信息，请先为字幕分配说话人');
                        return;
                    }
                    
                    const confirmed = confirm('开始智能配音？这将为每个说话人分配不同的音色，并去除原视频人声。');
                    if (!confirmed) return;
                    
                    // 将字幕数据转换为完整的JSON格式，包含所有必要的时间字段
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
                    
                    console.log('发送配音请求数据:', payload);
                    
                    const res = await fetch(`/viewer_api/${taskId}/voice_dubbing`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });
                    const data = await res.json();
                    if (data && data.code === 0) {
                        alert('配音任务已启动，请稍后查看结果');
                        // 可以跳转到任务状态页面
                        window.open(`/dubbing_result/${data.task_id}`, '_blank');
                    } else {
                        alert(data && data.msg ? data.msg : '配音启动失败');
                    }
                } catch (e) {
                    console.error(e);
                    alert('配音启动失败');
                }
            }

            btnVoiceDubbing.addEventListener('click', onVoiceDubbing);
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
        for f in files:
            lower = f.name.lower()
            if lower.endswith('.srt'):
                srt_path = f
            elif any(lower.endswith('.' + e) for e in exts):
                if video_path is None:
                    video_path = f
        if not srt_path or not video_path:
            return jsonify({"code": 1, "msg": "任务文件缺失（需要视频与srt）"}), 400

        # 解析字幕
        subs = help_srt.get_subtitle_from_srt(srt_path.as_posix())
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

        # 视频总时长（毫秒）
        try:
            video_ms = int(help_ffmpeg.get_video_duration(video_path.as_posix()) or 0)
        except Exception:
            video_ms = parsed[-1]['end'] if parsed else 0

        return jsonify({"code": 0, "msg": "ok", "subtitles": parsed, "video_ms": video_ms, "speakers": sorted(list(spk_set))})

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

        out_name = f'edited_{int(time.time())}.srt'
        out_path = (task_dir / out_name).as_posix()
        Path(out_path).write_text(srt_str, encoding='utf-8')

        download_url = f'/{API_RESOURCE}/{task_id}/{out_name}'
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
            
            # 使用Demucs分离
            print("执行Demucs分离...")
            demucs_args = [
                *demucs_cmd,
                '--two-stems', 'vocals',  # 分离人声和背景
                '--out', str(output_path),
                str(audio_path)
            ]
            
            print(f"执行命令: {' '.join(demucs_args)}")
            result = subprocess.run(demucs_args, capture_output=True, text=True)
            
            if result.returncode != 0:
                print(f"Demucs分离失败: {result.stderr}")
                return False
            
            # Demucs输出目录结构
            audio_name = Path(audio_path).stem
            demucs_output_dir = output_path / "htdemucs" / audio_name
            
            # 查找分离后的文件
            vocals_file = demucs_output_dir / "vocals.wav"
            no_vocals_file = demucs_output_dir / "no_vocals.wav"
            
            if vocals_file.exists() and no_vocals_file.exists():
                # 复制到指定位置
                shutil.copy2(vocals_file, vocal_path)
                shutil.copy2(no_vocals_file, background_path)
                
                print(f"人声分离成功: {vocal_path}")
                print(f"背景音分离成功: {background_path}")
                
                # 清理Demucs临时文件
                if demucs_output_dir.exists():
                    shutil.rmtree(demucs_output_dir)
                
                return True
            else:
                print("Demucs输出文件未找到")
                print(f"查找路径: {demucs_output_dir}")
                return False
                
        except Exception as e:
            print(f"Demucs分离异常: {str(e)}")
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
                '-filter_complex', '[0:a][1:a]amix=inputs=2:duration=longest[mixed]',
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
            
            # 直接将配音音频与原视频画面合成
            cmd = [
                'ffmpeg', '-y',
                '-i', video_path,
                '-i', dubbing_path,
                '-c:v', 'copy',  # 复制视频流，不重新编码
                '-c:a', 'aac',
                '-b:a', '128k',
                '-map', '0:v:0',  # 使用原视频的画面
                '-map', '1:a:0',  # 使用配音音频
                '-shortest',  # 以较短的流为准
                str(output_path)
            ]
            subprocess.run(cmd, check=True, capture_output=True)
            print(f"简化版视频合成完成: {output_path}")
                
        except Exception as e:
            print(f"简化版视频合成失败: {str(e)}")
            # 如果合成失败，至少保留配音音频文件

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

    # 第1个接口 /tts
    """
    根据字幕合成配音接口
    
    请求数据类型: Content-Type:application
    
    请求参数：
    
    name:必须参数，字符串类型，需要配音的srt字幕的绝对路径(需同本软件在同一设备)，或者直接传递合法的srt字幕格式内容
    tts_type:必须参数，数字类型，配音渠道，0="Edge-TTS",1='CosyVoice',2="ChatTTS",3=302.AI,4="FishTTS",5="Azure-TTS",
        6="GPT-SoVITS",7="clone-voice",8="OpenAI TTS",9="Elevenlabs.io",10="Google TTS",11="自定义TTS API"
    voice_role:必须参数，字符串类型，对应配音渠道的角色名，edge-tts/azure-tts/302.ai(azure模型)时目标语言不同，角色名也不同，具体见底部
    target_language:必须参数，字符串类型，需要配音的语言类型代码，即所传递的字幕文字语言代码，可选值 简体中文zh-cn，繁体zh-tw，英语en，法语fr，德语de，日语ja，韩语ko，俄语ru，西班牙语es，泰国语th，意大利语it，葡萄牙语pt，越南语vi，阿拉伯语ar，土耳其语tr，印地语hi，匈牙利语hu，乌克兰语uk，印尼语id，马来语ms，哈萨克语kk，捷克语cs，波兰语pl，荷兰语nl，瑞典语sv
    voice_rate:可选参数，字符串类型，语速加减值，格式为：加速`+数字%`，减速`-数字%`
    volume:可选参数，字符串类型，音量变化值(仅配音渠道为edge-tts生效)，格式为 增大音量`+数字%`，降低音量`-数字%`
    pitch:可选参数，字符串类型，音调变化值(仅配音渠道为edge-tts生效)，格式为 调大音调`+数字Hz`,降低音量`-数字Hz`
    out_ext:可选参数，字符串类型，输出配音文件类型，mp3|wav|flac|aac,默认wav
    voice_autorate:可选参数，布尔类型，默认False，是否自动加快语速，以便与字幕对齐
    
    返回数据：
    返回类型：json格式，
    成功时返回，可根据task_id通过 task_status 获取任务进度
    {"code":0,"msg":"ok","task_id":任务id}
    
    失败时返回
    {"code":1,"msg":"错误信息"}
    
    
    请求示例
    ```
    def test_tts():
        res=requests.post("http://127.0.0.1:9011/tts",json={
        "name":"C:/users/c1/videos/zh0.srt",
        "voice_role":"zh-CN-YunjianNeural",
        "target_language_code":"zh-cn",
        "voice_rate":"+0%",
        "volume":"+0%",
        "pitch":"+0Hz",
        "tts_type":"0",
        "out_ext":"mp3",
        "voice_autorate":True,
        })
        print(res.json())
    ```
    """
    @app.route('/tts', methods=['POST'])
    def tts():
        data = request.json
        # 从请求数据中获取参数
        name = data.get('name', '').strip()
        if not name:
            return jsonify({"code": 1, "msg": "The parameter name is not allowed to be empty"})
        is_srt=True
        if name.find("\n") == -1 and name.endswith('.srt'):
            if not Path(name).exists():
                return jsonify({"code": 1, "msg": f"The file {name} is not exist"})
        else:
            tmp_file = config.TEMP_DIR + f'/tts-srt-{time.time()}-{random.randint(1, 9999)}.srt'
            is_srt=tools.is_srt_string(name)
            Path(tmp_file).write_text(tools.process_text_to_srt_str(name) if not is_srt else name, encoding='utf-8')
            name = tmp_file

        cfg={
            "name":name,
            "voice_role":data.get("voice_role"),
            "target_language_code":data.get('target_language_code'),
            "tts_type":int(data.get('tts_type',0)),
            "voice_rate":data.get('voice_rate',"+0%"),
            "volume":data.get('volume',"+0%"),
            "pitch":data.get('pitch',"+0Hz"),
            "out_ext":data.get('out_ext',"mp3"),
            "voice_autorate":bool(data.get('voice_autorate',False)) if is_srt else False,
        }
        is_allow_lang=tts_model.is_allow_lang(langcode=cfg['target_language_code'],tts_type=cfg['tts_type'])
        if is_allow_lang is not True:
            return jsonify({"code":4,"msg":is_allow_lang})
        is_input_api=tts_model.is_input_api(tts_type=cfg['tts_type'],return_str=True)
        if is_input_api is not True:
            return jsonify({"code":5,"msg":is_input_api})


        obj = tools.format_video(name, None)
        obj['target_dir'] = TARGET_DIR + f'/{obj["uuid"]}'
        obj['cache_folder'] = config.TEMP_DIR + f'/{obj["uuid"]}'
        Path(obj['target_dir']).mkdir(parents=True, exist_ok=True)
        cfg.update(obj)

        config.box_tts = 'ing'
        trk = DubbingSrt(cfg)
        config.dubb_queue.append(trk)
        tools.set_process(text=f"Currently in queue No.{len(config.dubb_queue)}",uuid=obj['uuid'])
        return jsonify({'code': 0, 'task_id': obj['uuid']})


    # 第2个接口 /translate_srt
    """
    字幕翻译接口
    
    请求参数:
    类型 Content-Type:application/json
    
    请求数据:
    name:必须参数，字符串类型，需要翻译的srt字幕的绝对路径(需同本软件在同一设备)，或者直接传递合法的srt字幕格式内容
    translate_type：必须参数，整数类型，翻译渠道
    target_language:必须参数，字符串类型，要翻译到的目标语言代码。可选值 简体中文zh-cn，繁体zh-tw，英语en，法语fr，德语de，日语ja，韩语ko，俄语ru，西班牙语es，泰国语th，意大利语it，葡萄牙语pt，越南语vi，阿拉伯语ar，土耳其语tr，印地语hi，匈牙利语hu，乌克兰语uk，印尼语id，马来语ms，哈萨克语kk，捷克语cs，波兰语pl，荷兰语nl，瑞典语sv
    source_code:可选参数，字符串类型，原始字幕语言代码，可选同上
    
    返回数据
    返回类型：json格式，
    成功时返回，可根据task_id通过 task_status 获取任务进度
    {"code":0,"msg":"ok","task_id":任务id}
    
    失败时返回
    {"code":1,"msg":"错误信息"}
    
    请求示例
    ```
    def test_translate_srt():
        res=requests.post("http://127.0.0.1:9011/translate_srt",json={
        "name":"C:/users/c1/videos/zh0.srt",
        "target_language":"en",
        "translate_type":0
        })
        print(res.json())
    ```
    
    """
    @app.route('/translate_srt', methods=['POST'])
    def translate_srt():
        data = request.json
        # 从请求数据中获取参数
        name = data.get('name', '').strip()
        if not name:
            return jsonify({"code": 1, "msg": "The parameter name is not allowed to be empty"})
        is_srt=True
        if name.find("\n") == -1  and name.endswith('.srt'):
            if not Path(name).exists():
                return jsonify({"code": 1, "msg": f"The file {name} is not exist"})
        else:
            tmp_file = config.TEMP_DIR + f'/trans-srt-{time.time()}-{random.randint(1, 9999)}.srt'
            is_srt=tools.is_srt_string(name)
            Path(tmp_file).write_text(tools.process_text_to_srt_str(name) if not is_srt else name, encoding='utf-8')
            name = tmp_file

        cfg = {
            "translate_type": int(data.get('translate_type', 0)),
            "text_list": tools.get_subtitle_from_srt(name),
            "target_code": data.get('target_language'),
            "source_code": data.get('source_code', '')
        }
        is_allow=translator.is_allow_translate(translate_type=cfg['translate_type'],show_target=cfg['target_code'],return_str=True)
        if is_allow is not True:
            return jsonify({"code":5,"msg":is_allow})
        obj = tools.format_video(name, None)
        obj['target_dir'] = TARGET_DIR + f'/{obj["uuid"]}'
        obj['cache_folder'] = config.TEMP_DIR + f'/{obj["uuid"]}'
        Path(obj['target_dir']).mkdir(parents=True, exist_ok=True)
        cfg.update(obj)

        config.box_trans = 'ing'
        trk = TranslateSrt(cfg)
        config.trans_queue.append(trk)
        tools.set_process(text=f"Currently in queue No.{len(config.trans_queue)}",uuid=obj['uuid'])
        return jsonify({'code': 0, 'task_id': obj['uuid']})


    # 第3个接口 /recogn
    """
    语音识别、音视频转字幕接口
    
    请求参数:
    类型 Content-Type:application/json
    
    请求数据:
    name:必须参数，字符串类型，需要翻译的音频或视频的绝对路径(需同本软件在同一设备)
    recogn_type:必须参数，数字类型，语音识别模式，0=faster-whisper本地模型识别，1=openai-whisper本地模型识别，2=Google识别api，3=zh_recogn中文识别，4=豆包模型识别，5=自定义识别API，6=OpenAI识别API
    model_name:必须参数faster-whisper和openai-whisper模式时的模型名字
    detect_language:必须参数，字符串类型，音视频中人类说话语言。中文zh，英语en，法语fr，德语de，日语ja，韩语ko，俄语ru，西班牙语es，泰国语th，意大利语it，葡萄牙语pt，越南语vi，阿拉伯语ar，土耳其语tr，印地语hi，匈牙利语hu，乌克兰语uk，印尼语id，马来语ms，哈萨克语kk，捷克语cs，波兰语pl，荷兰语nl，瑞典语sv
    split_type：可选参数，字符串类型，默认all：整体识别，可选avg：均等分割
    is_cuda:可选参数，布尔类型，是否启用CUDA加速，默认False
    
    返回数据
    返回类型：json格式，
    成功时返回，可根据task_id通过 task_status 获取任务进度
    {"code":0,"msg":"ok","task_id":任务id}
    
    失败时返回
    {"code":1,"msg":"错误信息"}
    
    示例
    def test_recogn():
        res=requests.post("http://127.0.0.1:9011/recogn",json={
        "name":"/Users/duanyanbiao/Downloads/testtesttest.mp4",
        "recogn_type":0,
        "split_type":"all",
        "model_name":"tiny",
        "is_cuda":False,
        "detect_language":"zh",
        })
        print(res.json())
    
    """
    @app.route('/recogn', methods=['POST'])
    def recogn():
        data = request.json
        # 从请求数据中获取参数
        name = data.get('name', '').strip()
        if not name:
            return jsonify({"code": 1, "msg": "The parameter name is not allowed to be empty"})
        if not Path(name).is_file():
            return jsonify({"code": 1, "msg": f"The file {name} is not exist"})

        cfg = {
            "recogn_type": int(data.get('recogn_type', 0)),
            "split_type": data.get('split_type', 'all'),
            "model_name": data.get('model_name', 'tiny'),
            "is_cuda": bool(data.get('is_cuda', False)),
            "detect_language": data.get('detect_language', '')
        }

        is_allow=recognition.is_allow_lang(langcode=cfg['detect_language'],recogn_type=cfg['recogn_type'])
        if is_allow is not True:
            return jsonify({"code":5,"msg":is_allow})

        is_input=recognition.is_input_api(recogn_type=cfg['recogn_type'],return_str=True)
        if is_input is not True:
            return jsonify({"code":5,"msg":is_input})


        obj = tools.format_video(name, None)
        obj['target_dir'] = TARGET_DIR + f'/{obj["uuid"]}'
        obj['cache_folder'] = config.TEMP_DIR + f'/{obj["uuid"]}'
        Path(obj['target_dir']).mkdir(parents=True, exist_ok=True)
        cfg.update(obj)
        config.box_recogn = 'ing'
        trk = SpeechToText(cfg)
        config.prepare_queue.append(trk)
        tools.set_process(text=f"Currently in queue No.{len(config.prepare_queue)}",uuid=obj['uuid'])
        return jsonify({'code': 0, 'task_id': obj['uuid']})


    # 第4个接口
    """
    视频完整翻译接口
    
    
    请求参数:
    类型 Content-Type:application/json
    
    请求数据:
    name:必须参数，字符串类型，需要翻译的音频或视频的绝对路径(需同本软件在同一设备)
    recogn_type:必须参数，数字类型，语音识别模式，0=faster-whisper本地模型识别，1=openai-whisper本地模型识别，2=Google识别api，3=zh_recogn中文识别，4=豆包模型识别，5=自定义识别API，6=OpenAI识别API
    model_name:必须参数faster-whisper和openai-whisper模式时的模型名字
    split_type：可选参数，字符串类型，默认all：整体识别，可选avg：均等分割
    is_cuda:可选参数，布尔类型，是否启用CUDA加速，默认False
    translate_type：必须参数，整数类型，翻译渠道
    target_language:必须参数，字符串类型，要翻译到的目标语言代码。可选值 简体中文zh-cn，繁体zh-tw，英语en，法语fr，德语de，日语ja，韩语ko，俄语ru，西班牙语es，泰国语th，意大利语it，葡萄牙语pt，越南语vi，阿拉伯语ar，土耳其语tr，印地语hi，匈牙利语hu，乌克兰语uk，印尼语id，马来语ms，哈萨克语kk，捷克语cs，波兰语pl，荷兰语nl，瑞典语sv
    source_language:可选参数，字符串类型，原始字幕语言代码，可选同上
    tts_type:必须参数，数字类型，配音渠道，0="Edge-TTS",1='CosyVoice',2="ChatTTS",3=302.AI,4="FishTTS",5="Azure-TTS",
        6="GPT-SoVITS",7="clone-voice",8="OpenAI TTS",9="Elevenlabs.io",10="Google TTS",11="自定义TTS API"
    voice_role:必须参数，字符串类型，对应配音渠道的角色名，edge-tts/azure-tts/302.ai(azure模型)时目标语言不同，角色名也不同，具体见底部
    voice_rate:可选参数，字符串类型，语速加减值，格式为：加速`+数字%`，减速`-数字%`
    volume:可选参数，字符串类型，音量变化值(仅配音渠道为edge-tts生效)，格式为 增大音量`+数字%`，降低音量`-数字%`
    pitch:可选参数，字符串类型，音调变化值(仅配音渠道为edge-tts生效)，格式为 调大音调`+数字Hz`,降低音量`-数字Hz`
    out_ext:可选参数，字符串类型，输出配音文件类型，mp3|wav|flac|aac,默认wav
    voice_autorate:可选参数，布尔类型，默认False，是否自动加快语速，以便与字幕对齐
    subtitle_type:可选参数，整数类型，默认0，字幕嵌入类型，0=不嵌入字幕，1=嵌入硬字幕，2=嵌入软字幕，3=嵌入双硬字幕，4=嵌入双软字幕
    append_video：可选参数，布尔类型，默认False，如果配音后音频时长大于视频，是否延长视频末尾
    only_video:可选参数，布尔类型，默认False，是否只生成视频文件，不生成字幕音频等
    
    返回数据
    返回类型：json格式，
    成功时返回，可根据task_id通过 task_status 获取任务进度
    {"code":0,"msg":"ok","task_id":任务id}
    
    失败时返回
    {"code":1,"msg":"错误信息"}
    
    示例
    def test_trans_video():
        res=requests.post("http://127.0.0.1:9011/trans_video",json={
        "name":"C:/Users/c1/Videos/10ass.mp4",
    
        "recogn_type":0,
        "split_type":"all",
        "model_name":"tiny",
    
        "translate_type":0,
        "source_language":"zh-cn",
        "target_language":"en",
    
        "tts_type":0,
        "voice_role":"zh-CN-YunjianNeural",
        "voice_rate":"+0%",
        "volume":"+0%",
        "pitch":"+0Hz",
        "voice_autorate":True,
        "video_autorate":True,
    
        "is_separate":False,
        "back_audio":"",
        
        "subtitle_type":1,
        "append_video":False,
    
        "is_cuda":False,
        })
        print(res.json())
    
    """
    @app.route('/trans_video', methods=['POST'])
    def trans_video():
        data = request.json
        name = data.get('name', '')
        if not name:
            return jsonify({"code": 1, "msg": "The parameter name is not allowed to be empty"})
        if not Path(name).exists():
            return jsonify({"code": 1, "msg": f"The file {name} is not exist"})

        cfg = {
            # 通用
            "name": name,

            "is_separate": bool(data.get('is_separate', False)),
            "back_audio": data.get('back_audio', ''),

            # 识别
            "recogn_type": int(data.get('recogn_type', 0)),
            "split_type": data.get('split_type','all'),
            "model_name": data.get('model_name','tiny'),
            "cuda": bool(data.get('is_cuda',False)),

            "subtitles": data.get("subtitles", ""),

            # 翻译
            "translate_type": int(data.get('translate_type', 0)),
            "target_language": data.get('target_language'),
            "source_language": data.get('source_language'),

            # 配音
            "tts_type": int(data.get('tts_type', 0)),
            "voice_role": data.get('voice_role',''),
            "voice_rate": data.get('voice_rate','+0%'),
            "voice_autorate": bool(data.get('voice_autorate', False)),
            "video_autorate": bool(data.get('video_autorate', False)),
            "volume": data.get('volume','+0%'),
            "pitch": data.get('pitch','+0Hz'),

            "subtitle_type": int(data.get('subtitle_type', 0)),
            "append_video": bool(data.get('append_video', False)),

            "is_batch": True,
            "app_mode": "biaozhun",

            "only_video": bool(data.get('only_video', False))

        }
        if not cfg['subtitles']:
            is_allow = recognition.is_allow_lang(langcode=cfg['target_language'], recogn_type=cfg['recogn_type'])
            if is_allow is not True:
                return jsonify({"code": 5, "msg": is_allow})

            is_input = recognition.is_input_api(recogn_type=cfg['recogn_type'], return_str=True)
            if is_input is not True:
                return jsonify({"code": 5, "msg": is_input})
        if cfg['source_language'] != cfg['target_language']:
            is_allow=translator.is_allow_translate(translate_type=cfg['translate_type'],show_target=cfg['target_language'],return_str=True)
            if is_allow is not True:
                return jsonify({"code":5,"msg":is_allow})

        if cfg['voice_role'] and cfg['voice_role'].lower()!='no' and cfg['target_language']:
            is_allow_lang = tts_model.is_allow_lang(langcode=cfg['target_language'], tts_type=cfg['tts_type'])
            if is_allow_lang is not True:
                return jsonify({"code": 4, "msg": is_allow_lang})
            is_input_api = tts_model.is_input_api(tts_type=cfg['tts_type'], return_str=True)
            if is_input_api is not True:
                return jsonify({"code": 5, "msg": is_input_api})



        obj = tools.format_video(name, None)
        obj['target_dir'] = TARGET_DIR + f'/{obj["uuid"]}'
        obj['cache_folder'] = config.TEMP_DIR + f'/{obj["uuid"]}'
        Path(obj['target_dir']).mkdir(parents=True, exist_ok=True)
        cfg.update(obj)

        config.current_status = 'ing'
        trk = TransCreate(cfg)
        config.prepare_queue.append(trk)
        tools.set_process(text=f"Currently in queue No.{len(config.prepare_queue)}",uuid=obj['uuid'])
        #
        return jsonify({'code': 0, 'task_id': obj['uuid']})


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
