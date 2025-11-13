from pathlib import Path
from flask import Blueprint, jsonify, render_template_string, request
from videotrans.configure import config
from videotrans.util import tools

voice_bp = Blueprint('voice_manager', __name__, url_prefix='/voice_manager')


@voice_bp.route('', methods=['GET'])
def voice_manager_page():
    task_id = (request.args.get('task_id') or '').strip()
    html = """
{% raw %}
<!doctype html>
<html lang="zh">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>ElevenLabs 语音管理</title>
  <style>
    body { margin:0; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial; }
    header { padding:12px 16px; border-bottom:1px solid #eee; display:flex; align-items:center; gap:12px; }
    main { padding:16px; }
    .btn { padding:6px 10px; border:1px solid #666; background:#fff; border-radius:6px; cursor:pointer; font-size:13px; }
    .btn.danger { border-color:#c62828; color:#fff; background:#c62828; }
    .btn.primary { border-color:#007aff; color:#fff; background:#007aff; }
    .btn.success { border-color:#4caf50; color:#fff; background:#4caf50; }
    .list { max-width:980px; border:1px solid #eee; border-radius:8px; overflow:hidden; }
    .row { display:flex; align-items:center; gap:8px; padding:8px 10px; border-bottom:1px solid #eee; }
    .row:last-child { border-bottom:none; }
    .name { font-weight:600; }
    .sub { color:#777; font-size:12px; }
    .toolbar { display:flex; gap:8px; align-items:center; }
    
    /* Tab styles */
    .tabs { display: flex; margin-bottom: 20px; border-bottom: 1px solid #eee; }
    .tab { padding: 10px 20px; cursor: pointer; border: 1px solid transparent; border-bottom: none; border-radius: 5px 5px 0 0; }
    .tab.active { border-color: #eee; background: #f5f5f5; }
    .tab-content { display: none; }
    .tab-content.active { display: block; }
    
    /* Modal styles */
    .modal { display: none; position: fixed; z-index: 1000; left: 0; top: 0; width: 100%; height: 100%; background-color: rgba(0,0,0,0.4); }
    .modal-content { background-color: #fefefe; margin: 15% auto; padding: 20px; border: 1px solid #888; border-radius: 8px; width: 80%; max-width: 500px; }
    .close { color: #aaa; float: right; font-size: 28px; font-weight: bold; cursor: pointer; }
    .close:hover { color: black; }
    .form-group { margin-bottom: 15px; }
    .form-group label { display: block; margin-bottom: 5px; font-weight: bold; }
    .form-group input { width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px; box-sizing: border-box; }
    
    /* Loading spinner */
    .spinner {
      border: 4px solid #f3f3f3;
      border-top: 4px solid #3498db;
      border-radius: 50%;
      width: 40px;
      height: 40px;
      animation: spin 2s linear infinite;
      margin: 0 auto;
    }
    
    @keyframes spin {
      0% { transform: rotate(0deg); }
      100% { transform: rotate(360deg); }
    }
  </style>
</head>
<body>
  <header>
    <h3 style="margin:0;">语音管理</h3>
    <div style="color:#666; font-size:12px;">任务: <span id="taskIdText"></span></div>
    <div style="margin-left:auto; display:flex; gap:8px;">
      <button class="btn" onclick="history.back()">返回</button>
    </div>
  </header>
  <main>
    <!-- Tab navigation -->
    <div class="tabs">
      <div class="tab active" data-tab="voice-library">语音库管理</div>
      <div class="tab" data-tab="clone-voice">克隆声音</div>
    </div>
    
    <!-- Voice Library Tab -->
    <div id="voice-library" class="tab-content active">
      <h4>自定义语音（ElevenLabs）</h4>
      <div id="status" class="sub">加载中...</div>
      <div id="list" class="list" style="margin-bottom:18px;"></div>

      <h4>说话人音频（基于任务识别结果）</h4>
      <div style="margin:6px 0;">
        <button class="btn" id="btnScanSpeakers">扫描并生成说话人音频</button>
        <span id="spkStatus" class="sub"></span>
      </div>
      <div id="spkList" class="list"></div>
    </div>
    
    <!-- Clone Voice Tab -->
    <div id="clone-voice" class="tab-content">
      <h4>说话人音频列表</h4>
      <div id="clone-status" class="sub">加载中...</div>
      <div id="clone-list" class="list"></div>
    </div>
  </main>
  
  <!-- Clone Modal -->
  <div id="cloneModal" class="modal">
    <div class="modal-content">
      <span class="close">&times;</span>
      <h3>克隆声音</h3>
      <div class="form-group">
        <label for="voiceName">语音名称:</label>
        <input type="text" id="voiceName" placeholder="请输入语音名称">
      </div>
      <div class="form-group">
        <button id="previewMerged" class="btn">预览选中片段合并音频</button>
        <div id="mergedAudioContainer" style="margin-top: 10px; display: none;">
          <audio id="mergedAudio" controls style="width: 100%;"></audio>
        </div>
      </div>
      <button id="confirmClone" class="btn success">确认克隆</button>
    </div>
  </div>
  
  <!-- Edit Modal -->
  <div id="editModal" class="modal">
    <div class="modal-content">
      <span class="close">&times;</span>
      <h3>编辑声音名称</h3>
      <div class="form-group">
        <label for="editVoiceName">语音名称:</label>
        <input type="text" id="editVoiceName" placeholder="请输入新的语音名称">
        <input type="hidden" id="editVoiceId">
      </div>
      <button id="confirmEdit" class="btn primary">确认修改</button>
    </div>
  </div>
  
  <!-- Loading Modal -->
  <div id="loadingModal" class="modal">
    <div class="modal-content" style="text-align: center; padding: 30px;">
      <div class="spinner"></div>
      <p style="margin-top: 20px;">正在克隆声音，请稍候...</p>
    </div>
  </div>

  <script>
  const taskId = new URLSearchParams(location.search).get('task_id') || '';
  let currentSpeakerAudio = null;
  let currentSpeakerName = null;
  
  // Tab switching
  document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
      // Remove active class from all tabs and contents
      document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
      
      // Add active class to clicked tab and corresponding content
      tab.classList.add('active');
      const tabId = tab.getAttribute('data-tab');
      document.getElementById(tabId).classList.add('active');
      
      // Load data for active tab
      if (tabId === 'voice-library') {
        loadVoices();
        listSpeakers();
      } else if (tabId === 'clone-voice') {
        loadCloneSpeakers();
      }
    });
  });
  
  // Modal handling
  const modal = document.getElementById("cloneModal");
  const editModal = document.getElementById("editModal");
  const cloneModalClose = document.getElementsByClassName("close")[0];
  const editModalClose = document.getElementsByClassName("close")[1];
  
  cloneModalClose.onclick = function() {
    modal.style.display = "none";
  }
  
  editModalClose.onclick = function() {
    editModal.style.display = "none";
  }
  
  window.onclick = function(event) {
    if (event.target == modal) {
      modal.style.display = "none";
    } else if (event.target == editModal) {
      editModal.style.display = "none";
    }
  }
  
  document.getElementById("confirmClone").addEventListener("click", performClone);
  document.getElementById("previewMerged").addEventListener("click", previewMergedAudio);
  document.getElementById("confirmEdit").addEventListener("click", performEdit);
  
  try { document.getElementById('taskIdText').textContent = taskId; } catch(e) {}
  
  // Voice library functions (existing)
  async function loadVoices(){
    const status = document.getElementById('status');
    const list = document.getElementById('list');
    status.textContent = '加载中...';
    list.innerHTML = '';
    try{
      const r = await fetch(`/voice_manager/elevenlabs_voices?task_id=${taskId}`);
      const j = await r.json();
      if (!j || j.code !== 0){ status.textContent = (j&&j.msg)||'加载失败'; return; }
      const voices = Array.isArray(j.voices)? j.voices: [];
      if (voices.length === 0){ status.textContent = '未获取到声音'; return; }
      status.textContent = `共 {len_placeholder} 个声音`.replace('{len_placeholder}', voices.length);
      list.innerHTML = voices.map(v=>{
        const id = v.voice_id || v.id || '';
        const name = v.name || v.label || '(未命名)';
        const cat = v.category || '';
        return `<div class=\"row\">
          <div style=\"flex:1;\">
            <div class=\"name\">${name} <span class=\"sub\">(${id})</span></div>
            <div class=\"sub\">${cat}</div>
          </div>
          <button class=\"btn\" data-act=\"edit\" data-id=\"${id}\" data-name=\"${name}\">编辑</button>
          <button class=\"btn\" data-act=\"preview\" data-id=\"${id}\">试听</button>
          <button class=\"btn danger\" data-act=\"delete\" data-id=\"${id}\">删除</button>
        </div>`;
      }).join('');
    }catch(e){ status.textContent = '加载异常'; }
  }
  
  document.getElementById('list').addEventListener('click', async (e)=>{
    const t = e.target; if (!t || !t.dataset) return; const id = t.dataset.id; if (!id) return;
    if (t.dataset.act === 'delete'){
      if (!confirm('确认删除该自定义声音？')) return;
      const r = await fetch(`/voice_manager/elevenlabs_voice/${id}?task_id=${taskId}`, { method:'DELETE' });
      const j = await r.json();
      if (j && j.code === 0){ 
        alert('删除成功'); 
        // 刷新整个页面以显示删除后的结果
        loadVoices();
        // 如果在克隆声音标签页，也刷新克隆声音列表
        if (document.querySelector('[data-tab="clone-voice"]').classList.contains('active')) {
          loadCloneSpeakers();
        }
      } else { 
        alert((j&&j.msg)||'删除失败'); 
      }
    } else if (t.dataset.act === 'edit') {
      // 打开编辑模态框
      const editModal = document.getElementById('editModal');
      const editVoiceName = document.getElementById('editVoiceName');
      const editVoiceId = document.getElementById('editVoiceId');
      
      // 填充表单数据
      editVoiceName.value = t.dataset.name || '';
      editVoiceId.value = id;
      
      // 显示模态框
      editModal.style.display = 'block';
    } else if (t.dataset.act === 'preview'){
      // 创建或显示预览对话框
      let previewModal = document.getElementById('previewModal');
      if (!previewModal) {
        // 创建预览对话框
        previewModal = document.createElement('div');
        previewModal.id = 'previewModal';
        previewModal.className = 'modal';
        previewModal.innerHTML = `
          <div class="modal-content">
            <span class="close">&times;</span>
            <h3>试听声音</h3>
            <div class="form-group">
              <label for="previewText">输入要合成的文本：</label>
              <input type="text" id="previewText" placeholder="请输入要试听的文本" value="Hello, this is a preview.">
            </div>
            <button id="generatePreview" class="btn primary">生成试听</button>
            <div id="previewAudioContainer" style="margin-top: 15px; display: none;">
              <audio id="previewAudio" controls style="width: 100%;"></audio>
            </div>
          </div>
        `;
        document.body.appendChild(previewModal);
        
        // 添加关闭事件
        previewModal.querySelector('.close').onclick = function() {
          previewModal.style.display = "none";
        }
        window.onclick = function(event) {
          if (event.target == previewModal) {
            previewModal.style.display = "none";
          }
        }
        
        // 添加生成试听事件
        previewModal.querySelector('#generatePreview').addEventListener('click', async function() {
          const txt = document.getElementById('previewText').value;
          if (!txt) {
            alert('请输入要试听的文本');
            return;
          }
          
          // 显示加载状态
          const generateBtn = document.getElementById('generatePreview');
          const originalText = generateBtn.textContent;
          generateBtn.textContent = '生成中...';
          generateBtn.disabled = true;
          
          try {
            const r = await fetch(`/voice_manager/elevenlabs_tts_preview?task_id=${taskId}`, {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({ text: txt, voice_id: id })
            });
            const j = await r.json();
            if (j && j.code === 0 && j.audio_url) {
              // 显示音频播放器
              const container = document.getElementById('previewAudioContainer');
              const audio = document.getElementById('previewAudio');
              audio.src = j.audio_url;
              container.style.display = 'block';
              // 自动播放
              audio.play();
            } else {
              alert(j?.msg || '试听失败');
            }
          } catch (error) {
            alert('试听过程中出现错误: ' + error.message);
          } finally {
            // 恢复按钮状态
            generateBtn.textContent = originalText;
            generateBtn.disabled = false;
          }
        });
      }
      
      // 显示对话框
      previewModal.style.display = "block";
      // 清空之前的音频
      document.getElementById('previewAudioContainer').style.display = 'none';
      // 设置默认文本
      document.getElementById('previewText').value = 'Hello, this is a preview.';
    }
  });
  
  // Speaker audio functions (existing)
  async function listSpeakers(){
    const st = document.getElementById('spkStatus');
    const box = document.getElementById('spkList');
    st.textContent = '加载中...';
    box.innerHTML = '';
    try{
      const r = await fetch(`/voice_manager/speakers_list?task_id=${taskId}`);
      const j = await r.json();
      if (!j || j.code !== 0){ st.textContent = (j&&j.msg)||'加载失败'; return; }
      const groups = j.groups || [];
      if (groups.length === 0){ st.textContent = '未找到说话人音频'; return; }
      st.textContent = `共 ${groups.length} 个说话人`;
      box.innerHTML = groups.map(g=>{
        const name = g.speaker || '未知';
        const url = g.audio_url || '';
        return `<div class=\"row\">\n              <div style=\"flex:1;\"><div class=\"name\">${name}</div></div>\n              ${url?`<audio controls src=\"${url}\"></audio>`:''}
        </div>`;
      }).join('');
    }catch(e){ st.textContent='加载异常'; }
  }

  async function scanSpeakers(){
    const st = document.getElementById('spkStatus');
    st.textContent = '扫描并生成中...';
    try{
      const r = await fetch(`/voice_manager/speakers_extract?task_id=${taskId}`, { method:'POST' });
      const j = await r.json();
      if (!j || j.code !== 0){ st.textContent = (j&&j.msg)||'生成失败'; return; }
      st.textContent = '生成完成';
      listSpeakers();
    }catch(e){ st.textContent = '生成异常'; }
  }
  document.getElementById('btnScanSpeakers').addEventListener('click', scanSpeakers);
  
  // Initialize first tab
  loadVoices();
  listSpeakers();
  
    // Clone voice functions (new)
  async function loadCloneSpeakers(){
    const status = document.getElementById('clone-status');
    const list = document.getElementById('clone-list');
    status.textContent = '加载中...';
    list.innerHTML = '';
    try{
      const r = await fetch(`/voice_manager/speakers_list?task_id=${taskId}`);
      const j = await r.json();
      if (!j || j.code !== 0){ status.textContent = (j&&j.msg)||'加载失败'; return; }
      
      // Group segments by speaker
      const speakers = {};
      const segments = j.segments || [];
      segments.forEach(segment => {
        const speaker = segment.speaker || '未知';
        if (!speakers[speaker]) {
          speakers[speaker] = [];
        }
        speakers[speaker].push(segment);
      });
      
      const speakerNames = Object.keys(speakers);
      if (speakerNames.length === 0){ status.textContent = '未找到说话人音频'; return; }
      status.textContent = `共 ${speakerNames.length} 个说话人`;
      
      // Create HTML for each speaker
      list.innerHTML = speakerNames.map(speakerName => {
        const segments = speakers[speakerName];
      const segmentsHtml = segments.map((segment, index) => {
        const audioUrl = segment.audio_url || '';
        const startTime = new Date(segment.start_time).toISOString().substr(11, 12);
        const endTime = new Date(segment.end_time).toISOString().substr(11, 12);
        return `
          <div style="margin-bottom: 10px; display: flex; align-items: flex-start;">
            <input type="checkbox" id="${speakerName}_seg_${index}" data-speaker="${speakerName}" data-index="${index}" checked style="margin-right: 8px; margin-top: 10px;">
            <div style="flex: 1;">
              <label for="${speakerName}_seg_${index}" class="sub">片段 ${index + 1} (${startTime} - ${endTime})</label>
              ${audioUrl ? `<audio controls src="${audioUrl}" style="width:100%;"></audio>` : '<div class="sub">音频不可用</div>'}
            </div>
          </div>`;
      }).join('');
        
        return `
          <div class="row" style="flex-direction: column; align-items: flex-start;">
            <div style="width: 100%; display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
              <div class="name">${speakerName}</div>
              <div>
                <button class="btn primary" data-act="clone" data-speaker="${speakerName}">克隆</button>
                <button class="btn" data-act="extract" data-speaker="${speakerName}" style="margin-left: 8px;">提取说话人音频</button>
                <button class="btn" data-act="add-audio" data-speaker="${speakerName}" style="margin-left: 8px;">添加音频</button>
                <button class="btn" data-act="select-all" data-speaker="${speakerName}" style="margin-left: 8px;">全选</button>
                <button class="btn" data-act="deselect-all" data-speaker="${speakerName}" style="margin-left: 8px;">取消全选</button>
              </div>
            </div>
            <div style="width: 100%;">
              ${segmentsHtml}
            </div>
          </div>`;
      }).join('');
    }catch(e){ status.textContent='加载异常'; console.error(e); }
  }
  
  document.getElementById('clone-list').addEventListener('click', (e) => {
    const t = e.target;
    if (!t || !t.dataset) return;
    
    if (t.dataset.act === 'clone') {
      const speakerName = t.dataset.speaker;
      if (!speakerName) return;
      
      currentSpeakerName = speakerName;
      document.getElementById('voiceName').value = speakerName + '_克隆';
      modal.style.display = "block";
    } else if (t.dataset.act === 'extract') {
      const speakerName = t.dataset.speaker;
      if (!speakerName) return;
      
      // 调用后端API提取说话人音频
      extractSpeakerAudio(speakerName);
    } else if (t.dataset.act === 'add-audio') {
      const speakerName = t.dataset.speaker;
      if (!speakerName) return;
      
      // 创建文件上传输入框
      const fileInput = document.createElement('input');
      fileInput.type = 'file';
      fileInput.accept = 'audio/*';
      fileInput.style.display = 'none';
      
      // 添加文件选择事件监听器
      fileInput.addEventListener('change', async (event) => {
        const file = event.target.files[0];
        if (!file) return;
        
        // 创建FormData对象
        const formData = new FormData();
        formData.append('audio', file);
        formData.append('speaker', speakerName);
        
        try {
          // 显示上传提示
          const originalText = t.textContent;
          t.textContent = '上传中...';
          t.disabled = true;
          
          // 发送上传请求
          const response = await fetch(`/voice_manager/upload_speaker_audio?task_id=${taskId}`, {
            method: 'POST',
            body: formData
          });
          
          const result = await response.json();
          
          // 恢复按钮状态
          t.textContent = originalText;
          t.disabled = false;
          
          if (result && result.code === 0) {
            alert('音频上传成功');
            // 重新加载说话人列表以显示更新的音频
            loadCloneSpeakers();
          } else {
            alert(result?.msg || '上传失败');
          }
        } catch (error) {
          // 恢复按钮状态
          t.textContent = '添加音频';
          t.disabled = false;
          alert('上传过程中出现错误: ' + error.message);
        }
      });
      
      // 触发文件选择
      fileInput.click();
    } else if (t.dataset.act === 'select-all') {
      const speakerName = t.dataset.speaker;
      if (!speakerName) return;
      
      // 全选该说话人的所有片段
      const checkboxes = document.querySelectorAll(`input[data-speaker="${speakerName}"]`);
      checkboxes.forEach(checkbox => {
        checkbox.checked = true;
      });
    } else if (t.dataset.act === 'deselect-all') {
      const speakerName = t.dataset.speaker;
      if (!speakerName) return;
      
      // 取消全选该说话人的所有片段
      const checkboxes = document.querySelectorAll(`input[data-speaker="${speakerName}"]`);
      checkboxes.forEach(checkbox => {
        checkbox.checked = false;
      });
    }
  });
  
    async function extractSpeakerAudio(speakerName) {
      try {
        // 显示正在提取的提示
        alert('正在提取说话人音频，请稍候...');
        
        // 直接调用提取功能，它会自动处理音频片段的生成
        const response = await fetch(`/voice_manager/extract_speaker_audio?task_id=${taskId}`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json'
          },
          body: JSON.stringify({
            speaker: speakerName
          })
        });
        
        const result = await response.json();
        if (result && result.code === 0) {
          alert('提取成功，文件已保存到任务目录的speaker_audio文件夹中');
          // 重新加载说话人列表以显示更新的音频
          loadCloneSpeakers();
        } else {
          alert(result?.msg || '提取失败');
        }
      } catch (error) {
        alert('提取过程中出现错误: ' + error.message);
      }
    }
  
  async function performClone() {
    if (!currentSpeakerName) return;
    
    const voiceName = document.getElementById('voiceName').value.trim();
    if (!voiceName) {
      alert('请输入语音名称');
      return;
    }
    
    // 获取该说话人被勾选的片段索引
    const selectedSegments = [];
    const checkboxes = document.querySelectorAll(`input[data-speaker="${currentSpeakerName}"]`);
    checkboxes.forEach((checkbox, index) => {
      if (checkbox.checked) {
        selectedSegments.push(index);
      }
    });
    
    if (selectedSegments.length === 0) {
      alert('请至少选择一个音频片段');
      return;
    }
    
    // 显示loading对话框
    const loadingModal = document.getElementById("loadingModal");
    loadingModal.style.display = "block";
    
    try {
      // 合并该说话人的选定音频片段
      const response = await fetch(`/voice_manager/clone_voice?task_id=${taskId}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          speaker: currentSpeakerName,
          name: voiceName,
          segments: selectedSegments
        })
      });
      
      // 隐藏loading对话框
      loadingModal.style.display = "none";
      
      const result = await response.json();
      if (result && result.code === 0) {
        alert('克隆成功');
        modal.style.display = "none";
        // Reload voice library tab
        document.querySelector('[data-tab="voice-library"]').click();
      } else {
        // 检查是否是文件大小错误
        if (result?.msg?.includes('upload_file_size_exceeded')) {
          alert('克隆失败: 音频文件太大，请上传小于11MB的文件');
        } else {
          alert(result?.msg || '克隆失败');
        }
      }
    } catch (error) {
      // 隐藏loading对话框
      loadingModal.style.display = "none";
      alert('克隆过程中出现错误: ' + error.message);
    }
  }
  
  async function previewMergedAudio() {
    if (!currentSpeakerName) return;
    
    // 获取该说话人被勾选的片段索引
    const selectedSegments = [];
    const checkboxes = document.querySelectorAll(`input[data-speaker="${currentSpeakerName}"]`);
    checkboxes.forEach((checkbox, index) => {
      if (checkbox.checked) {
        selectedSegments.push(index);
      }
    });
    
    if (selectedSegments.length === 0) {
      alert('请至少选择一个音频片段');
      return;
    }
    
    try {
      // 显示正在生成预览的提示
      const previewButton = document.getElementById('previewMerged');
      const originalText = previewButton.textContent;
      previewButton.textContent = '生成预览中...';
      previewButton.disabled = true;
      
      // 调用后端API生成预览音频
      const response = await fetch(`/voice_manager/preview_merged_audio?task_id=${taskId}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          speaker: currentSpeakerName,
          segments: selectedSegments
        })
      });
      
      const result = await response.json();
      
      // 恢复按钮状态
      previewButton.textContent = originalText;
      previewButton.disabled = false;
      
      if (result && result.code === 0) {
        // 保存预览文件信息，供克隆使用
        currentSpeakerAudio = result.audio_url;
        // 显示音频播放器
        const container = document.getElementById('mergedAudioContainer');
        const audio = document.getElementById('mergedAudio');
        audio.src = result.audio_url;
        container.style.display = 'block';
      } else {
        alert(result?.msg || '生成预览失败');
      }
    } catch (error) {
      // 恢复按钮状态
      const previewButton = document.getElementById('previewMerged');
      previewButton.textContent = '预览选中片段合并音频';
      previewButton.disabled = false;
      alert('生成预览过程中出现错误: ' + error.message);
    }
  }
  
  // 编辑声音名称功能
  async function performEdit() {
    const editModal = document.getElementById('editModal');
    const editVoiceName = document.getElementById('editVoiceName');
    const editVoiceId = document.getElementById('editVoiceId');
    
    const voiceId = editVoiceId.value;
    const newName = editVoiceName.value.trim();
    
    if (!voiceId) {
      alert('无效的声音ID');
      return;
    }
    
    if (!newName) {
      alert('请输入新的语音名称');
      return;
    }
    
    try {
      // 显示加载状态
      const confirmButton = document.getElementById('confirmEdit');
      const originalText = confirmButton.textContent;
      confirmButton.textContent = '修改中...';
      confirmButton.disabled = true;
      
      // 调用后端API修改声音名称
      const response = await fetch(`/voice_manager/elevenlabs_voice/${voiceId}?task_id=${taskId}`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ name: newName })
      });
      
      const result = await response.json();
      
      // 恢复按钮状态
      confirmButton.textContent = originalText;
      confirmButton.disabled = false;
      
      if (result && result.code === 0) {
        alert('修改成功');
        // 关闭模态框
        editModal.style.display = 'none';
        // 重新加载声音列表
        loadVoices();
      } else {
        alert(result?.msg || '修改失败');
      }
    } catch (error) {
      // 恢复按钮状态
      const confirmButton = document.getElementById('confirmEdit');
      confirmButton.textContent = '确认修改';
      confirmButton.disabled = false;
      alert('修改过程中出现错误: ' + error.message);
    }
  }
  </script>
</body>
</html>
{% endraw %}
"""
    return render_template_string(html)

@voice_bp.route('/ping', methods=['GET'])
def voice_manager_ping():
    return jsonify({"code": 0, "msg": "ok"})


@voice_bp.route('/elevenlabs_voices', methods=['GET'])
def vm_elevenlabs_voices():
    task_id = (request.args.get('task_id') or '').strip()
    try:
        if not config.params.get('elevenlabstts_key'):
            return jsonify({"code": 1, "msg": "未配置ElevenLabs API密钥"}), 400
        from elevenlabs import ElevenLabs
        import httpx
        client = ElevenLabs(api_key=config.params['elevenlabstts_key'], httpx_client=httpx.Client())
        vs = client.voices.get_all()
        out = []
        private_cats = {'cloned', 'custom', 'owner', 'instant', 'cloned-by-user'}
        for v in getattr(vs, 'voices', []) or []:
            cat = (getattr(v, 'category', '') or '').lower()
            if cat not in private_cats:
                continue
            out.append({
                'voice_id': getattr(v, 'voice_id', ''),
                'name': getattr(v, 'name', ''),
                'category': getattr(v, 'category', ''),
                'preview_url': getattr(v, 'preview_url', ''),
            })
        return jsonify({"code": 0, "voices": out})
    except Exception as e:
        return jsonify({"code": 1, "msg": f"获取音色失败: {str(e)}"}), 500


@voice_bp.route('/elevenlabs_voice/<voice_id>', methods=['DELETE', 'PUT'])
def vm_delete_voice(voice_id):
    task_id = (request.args.get('task_id') or '').strip()
    
    # PUT请求用于编辑声音名称
    if request.method == 'PUT':
        try:
            if not config.params.get('elevenlabstts_key'):
                return jsonify({"code": 1, "msg": "未配置ElevenLabs API密钥"}), 400
            
            data = request.get_json(silent=True) or {}
            new_name = (data.get('name') or '').strip()
            
            if not new_name:
                return jsonify({"code": 1, "msg": "缺少新的声音名称"}), 400
            
            # 使用ElevenLabs SDK编辑声音名称
            from elevenlabs import ElevenLabs
            import httpx
            client = ElevenLabs(api_key=config.params['elevenlabstts_key'], httpx_client=httpx.Client())
            
            # 编辑声音名称
            client.voices.update(voice_id=voice_id, name=new_name)
            
            return jsonify({"code": 0, "msg": "修改成功"})
        except Exception as e:
            return jsonify({"code": 1, "msg": f"修改异常: {str(e)}"}), 500
    
    # DELETE请求用于删除声音
    try:
        if not config.params.get('elevenlabstts_key'):
            return jsonify({"code": 1, "msg": "未配置ElevenLabs API密钥"}), 400
        import requests
        headers = { 'xi-api-key': config.params['elevenlabstts_key'] }
        url = f"https://api.elevenlabs.io/v1/voices/{voice_id}"
        r = requests.delete(url, headers=headers, timeout=30)
        if r.status_code in (200,204):
            return jsonify({"code": 0, "msg": "删除成功"})
        return jsonify({"code": 1, "msg": f"删除失败: {r.status_code} {r.text}"}), 400
    except Exception as e:
        return jsonify({"code": 1, "msg": f"删除异常: {str(e)}"}), 500


@voice_bp.route('/elevenlabs_tts_preview', methods=['POST'])
def vm_tts_preview():
    task_id = (request.args.get('task_id') or '').strip()
    data = request.get_json(silent=True) or {}
    text = (data.get('text') or '').strip()
    voice_id = (data.get('voice_id') or '').strip()
    if not text or not voice_id:
        return jsonify({"code": 1, "msg": "缺少文本或voice_id"}), 400
    if not config.params.get('elevenlabstts_key'):
        return jsonify({"code": 1, "msg": "未配置ElevenLabs API密钥"}), 400
    try:
        import requests, uuid
        from videotrans.configure import config as _cfg
        root = config.ROOT_DIR
        api_res = 'apidata'
        target_dir = Path(root) / api_res / task_id
        target_dir.mkdir(parents=True, exist_ok=True)
        out_path = target_dir / f"preview_{uuid.uuid4().hex[:8]}.mp3"
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        headers = {
            'xi-api-key': config.params['elevenlabstts_key'],
            'accept': 'audio/mpeg',
            'content-type': 'application/json'
        }
        payload = {
            'text': text,
            'model_id': 'eleven_multilingual_v2'
        }
        r = requests.post(url, headers=headers, json=payload, timeout=60)
        if r.status_code != 200:
            return jsonify({"code": 1, "msg": f"TTS失败: {r.status_code} {r.text}"}), 400
        with open(out_path, 'wb') as f:
            f.write(r.content)
        return jsonify({"code": 0, "audio_url": f'/{api_res}/{task_id}/{out_path.name}'})
    except Exception as e:
        return jsonify({"code": 1, "msg": f"试听异常: {str(e)}"}), 500


def _parse_srt_items(srt_path: Path):
    try:
        content = srt_path.read_text(encoding='utf-8')
    except Exception:
        content = srt_path.read_text(encoding='latin-1')
    import re
    blocks = re.split(r'\n\s*\n', content.strip())
    items = []
    def t2ms(t):
        t = t.replace('.', ',')
        h, m, s_ms = t.split(':')
        s, ms = s_ms.split(',')
        return (int(h)*3600 + int(m)*60 + int(s))*1000 + int(ms)
    for b in blocks:
        lines = [ln for ln in b.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
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
        # 尝试从多个位置匹配 [spkX]
        speaker = ''
        import re as _re
        # 首先尝试从时间行之前的行匹配
        if time_idx-1 >= 0:
            m = _re.search(r'\[(spk\d+)\]', lines[time_idx-1])
            if m:
                speaker = m.group(1)
        # 如果没找到，尝试从时间行匹配
        if not speaker:
            m = _re.search(r'\[(spk\d+)\]', time_line)
            if m:
                speaker = m.group(1)
        # 如果还没找到，尝试从文本行匹配
        if not speaker and text_lines:
            m = _re.search(r'\[(spk\d+)\]', text_lines[0])
            if m:
                speaker = m.group(1)
        # 如果仍然没找到，使用默认值
        if not speaker:
            speaker = 'spk0'
        items.append({'start_time': st, 'end_time': et, 'text': text, 'speaker': speaker})
    return items


def _extract_speakers_audio(task_id: str):
    task_dir = Path(config.ROOT_DIR) / 'apidata' / task_id
    if not task_dir.exists():
        return False, '任务不存在', []
    # 字幕文件优先：raw.srt
    srt = task_dir / 'raw.srt'
    if not srt.exists():
        # 退而求其次：任意 srt
        cands = list(task_dir.glob('*.srt'))
        if cands:
            srt = cands[0]
    if not srt.exists():
        return False, '未找到字幕文件', []
    items = _parse_srt_items(srt)
    if not items:
        return False, '字幕为空', []
    # 分组
    groups = {}
    for it in items:
        spk = it.get('speaker') or 'spk0'
        groups.setdefault(spk, []).append(it)
    # 人声音频
    vocals = task_dir / 'audio_vocals.wav'
    if not vocals.exists():
        # 若不存在，则尝试从上传音频抽取全音轨作为替代
        up = None
        for f in task_dir.iterdir():
            if f.is_file() and f.suffix.lower() in {'.mp3','.wav','.m4a','.flac','.aac','.wma','.ogg'}:
                up = f; break
        if up:
            try:
                tools.runffmpeg(['-y','-i', up.as_posix(), vocals.as_posix()])
            except Exception:
                pass
    if not vocals.exists():
        return False, '未找到人声音频 audio_vocals.wav', []
    spkdir = task_dir / 'speaker_audio'
    spkdir.mkdir(exist_ok=True)
    results = []
    for spk, segs in groups.items():
        # 为每个片段生成单独的音频文件
        for i, seg in enumerate(segs):
            st = seg['start_time']/1000.0
            et = seg['end_time']/1000.0
            dur = max(0.0, et - st)
            if dur <= 0: continue

            segf = spkdir / f"{spk}_seg_{i}.mp3"
            # 检查文件是否已存在，如果不存在则生成
            if not segf.exists():
                tools.runffmpeg(['-y','-i', vocals.as_posix(), '-ss', str(st), '-t', str(dur), '-f', 'mp3', '-ab', '64k', segf.as_posix()])
            # 添加到结果中
            results.append({'speaker': spk, 'audio_url': f"/apidata/{task_id}/speaker_audio/{segf.name}"})
    return True, 'ok', results


@voice_bp.route('/speakers_extract', methods=['POST'])
def vm_speakers_extract():
    task_id = (request.args.get('task_id') or '').strip()
    ok, msg, groups = _extract_speakers_audio(task_id)
    if not ok:
        return jsonify({"code": 1, "msg": msg, "groups": groups}), 400
    return jsonify({"code": 0, "groups": groups})


@voice_bp.route('/speakers_list', methods=['GET'])
def vm_speakers_list():
    task_id = (request.args.get('task_id') or '').strip()
    task_dir = Path(config.ROOT_DIR) / 'apidata' / task_id
    srt_path = task_dir / 'raw.srt'
    
    # 如果没有raw.srt，尝试找其他srt文件
    if not srt_path.exists():
        srt_files = list(task_dir.glob('*.srt'))
        if srt_files:
            srt_path = srt_files[0]
    
    # 初始化segments列表
    segments = []
    
    # 如果存在SRT文件，解析它获取说话人和时间段
    if srt_path.exists():
        segments = _parse_srt_items(srt_path)
    
    # 按说话人分组
    speaker_segments = {}
    for segment in segments:
        speaker = segment.get('speaker') or 'spk0'
        if speaker not in speaker_segments:
            speaker_segments[speaker] = []
        speaker_segments[speaker].append(segment)
    
    # 获取音频片段文件目录
    speaker_audio_dir = task_dir / 'speaker_audio'
    

    # 如果speaker_audio目录存在，查找所有说话人的音频文件（包括上传的）
    if speaker_audio_dir.exists():
        # 遍历所有音频文件
        for audio_file in speaker_audio_dir.glob("*.mp3"):
            # 从文件名中提取说话人名称
            filename = audio_file.name
            if filename.startswith(("spk", "speaker")) and "_seg_" in filename:
                # 提取说话人名称（例如从"spk0_seg_0.mp3"中提取"spk0"）
                speaker = filename.split("_seg_")[0]
                
                # 如果这个说话人不在speaker_segments中，添加它
                if speaker not in speaker_segments:
                    speaker_segments[speaker] = []
    

    # 为每个说话人收集音频片段
    for speaker, segs in speaker_segments.items():
        # 获取该说话人的所有音频片段文件
        speaker_files = list(speaker_audio_dir.glob(f"{speaker}_seg_*.mp3"))
        # 按索引排序
        speaker_files.sort(key=lambda x: int(x.stem.split('_')[-1]))
        
        # 为每个音频文件创建或更新segment信息
        for i, audio_file in enumerate(speaker_files):
            # 生成音频URL
            audio_url = f"/apidata/{task_id}/speaker_audio/{audio_file.name}"
            
            # 如果是来自SRT的片段且索引匹配，更新其audio_url
            if i < len(segs):
                segs[i]['audio_url'] = audio_url
            else:
                # 如果是额外的上传文件，创建新的segment条目
                segs.append({
                    'speaker': speaker,
                    'audio_url': audio_url,
                    'start_time': 0,
                    'end_time': 0,
                    'text': f'上传的音频片段 {i+1}'
                })
    
    # 展平所有segments
    all_segments = []
    for segs in speaker_segments.values():
        all_segments.extend(segs)
    
    return jsonify({"code": 0, "groups": [{'speaker': k} for k in speaker_segments.keys()], "segments": all_segments})


@voice_bp.route('/clone_voice', methods=['POST'])
def vm_clone_voice():
    task_id = (request.args.get('task_id') or '').strip()
    data = request.get_json(silent=True) or {}
    speaker = (data.get('speaker') or '').strip()
    name = (data.get('name') or '').strip()
    segments = data.get('segments', [])  # 获取选定的片段索引
    
    if not speaker or not name:
        return jsonify({"code": 1, "msg": "缺少说话人或名称"}), 400
    
    if not config.params.get('elevenlabstts_key'):
        return jsonify({"code": 1, "msg": "未配置ElevenLabs API密钥"}), 400
    
    try:
        import uuid
        import time
        from elevenlabs import ElevenLabs
        import httpx
        from io import BytesIO
        
        # 获取说话人的音频片段目录
        task_dir = Path(config.ROOT_DIR) / 'apidata' / task_id
        speaker_audio_dir = task_dir / 'speaker_audio'
        
        if not speaker_audio_dir.exists():
            return jsonify({"code": 1, "msg": "说话人音频目录不存在"}), 400
        

        # 首先检查是否存在预览文件
        preview_files = list(speaker_audio_dir.glob(f"{speaker}_preview_*.mp3"))
        if preview_files:
            # 使用最新的预览文件
            preview_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
            merged_file = preview_files[0]
        else:
            # 如果没有预览文件，则重新生成合并文件

            # 获取说话人的所有音频片段文件
            segment_files = list(speaker_audio_dir.glob(f"{speaker}_seg_*.mp3"))
            if not segment_files:
                return jsonify({"code": 1, "msg": f"未找到说话人 {speaker} 的音频片段"}), 400
            
            # 如果指定了片段，则只选择这些片段
            if segments:
                # 根据索引过滤片段
                selected_files = []
                for index in segments:
                    # 查找匹配索引的文件
                    matched_files = [f for f in segment_files if f.name == f"{speaker}_seg_{index}.mp3"]
                    if matched_files:
                        selected_files.append(matched_files[0])
                
                if not selected_files:
                    return jsonify({"code": 1, "msg": f"未找到说话人 {speaker} 的选定音频片段"}), 400
                segment_files = selected_files
            
            # 按照索引排序
            segment_files.sort(key=lambda x: int(x.stem.split('_')[-1]))
            

            # 创建合并文件的列表
            concat_file = speaker_audio_dir / f"{speaker}_concat.txt"
            merged_file = speaker_audio_dir / f"{speaker}_merged_{uuid.uuid4().hex[:8]}.mp3"
            
            # 写入concat文件
            with open(concat_file, 'w') as f:
                for seg_file in segment_files:
                    f.write(f"file '{seg_file.as_posix()}'\n")
            
            # 使用ffmpeg合并音频文件并转换为MP3格式
            tools.runffmpeg(['-y', '-f', 'concat', '-safe', '0', '-i', concat_file.as_posix(), '-f', 'mp3', '-ab', '64k', merged_file.as_posix()])
            
            # 删除concat文件
            try:
                concat_file.unlink()
            except:
                pass
            
            # 检查合并后的文件是否存在
            if not merged_file.exists():
                return jsonify({"code": 1, "msg": "音频合并失败"}), 400
        
        # 检查音频文件长度
        import subprocess
        try:
            result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', merged_file.as_posix()], capture_output=True, text=True)
            duration = float(result.stdout.strip())
            print(f"Audio file duration: {duration} seconds")
            if duration < 6.0:
                return jsonify({"code": 1, "msg": f"音频文件长度不足6秒，当前长度：{duration:.2f}秒"}), 400
        except Exception as e:
            print(f"Error checking audio duration: {e}")
        

        # 检查音频文件大小 (ElevenLabs 限制为 11MB)
        file_size = merged_file.stat().st_size
        max_size = 11 * 1024 * 1024  # 11MB in bytes
        if file_size > max_size:
            # 删除合并后的临时文件（仅当不是预览文件时）
            if not str(merged_file).endswith('_preview_.mp3') and 'preview' not in str(merged_file):
                try:
                    merged_file.unlink()
                except:
                    pass
            return jsonify({"code": 1, "msg": f"音频文件大小超过11MB限制，请使用较短的音频片段。当前大小: {file_size / (1024*1024):.2f}MB"}), 400
        
        # 使用ElevenLabs SDK进行语音克隆
        try:
            # 创建ElevenLabs客户端
            client = ElevenLabs(
                api_key=config.params['elevenlabstts_key'],
                httpx_client=httpx.Client()
            )
            
            # 读取音频文件
            with open(merged_file, 'rb') as f:
                audio_data = f.read()
            
            # 创建语音克隆
            voice_name = name if name else f"{speaker}_clone_{int(time.time())}"
            
            # 使用instant voice cloning API
            voice = client.voices.ivc.create(
                name=voice_name,
                files=[BytesIO(audio_data)]
            )
            
            # 删除合并后的临时文件（仅当不是预览文件时）
            if not str(merged_file).endswith('_preview_.mp3') and 'preview' not in str(merged_file):
                try:
                    merged_file.unlink()
                except:
                    pass
            
            return jsonify({
                "code": 0, 
                "msg": "克隆成功", 
                "voice_id": voice.voice_id,
                "name": voice_name,
                "speaker": speaker
            })
            
        except Exception as e:
            print(f"创建语音克隆失败: {str(e)}")
            # 删除合并后的临时文件（仅当不是预览文件时）
            if not str(merged_file).endswith('_preview_.mp3') and 'preview' not in str(merged_file):
                try:
                    merged_file.unlink()
                except:
                    pass
            return jsonify({"code": 1, "msg": f"克隆失败: {str(e)}"}), 500
        
    except Exception as e:
        return jsonify({"code": 1, "msg": f"克隆异常: {str(e)}"}), 500


@voice_bp.route('/extract_speaker_audio', methods=['POST'])
def vm_extract_speaker_audio():
    task_id = (request.args.get('task_id') or '').strip()
    data = request.get_json(silent=True) or {}
    speaker = (data.get('speaker') or '').strip()
    
    if not speaker:
        return jsonify({"code": 1, "msg": "缺少说话人参数"}), 400
    
    try:
        # 获取说话人的所有音频片段
        task_dir = Path(config.ROOT_DIR) / 'apidata' / task_id
        speaker_audio_dir = task_dir / 'speaker_audio'
        

        # 如果目录不存在，先尝试生成音频片段
        if not speaker_audio_dir.exists():
            # 调用_extract_speakers_audio生成所有说话人的音频片段
            ok, msg, results = _extract_speakers_audio(task_id)
            if not ok:
                return jsonify({"code": 1, "msg": f"生成音频片段失败: {msg}"}), 400
        else:
            # 检查指定说话人的音频片段是否存在
            segment_files = list(speaker_audio_dir.glob(f"{speaker}_seg_*.mp3"))
        if not segment_files:
            # 如果没有找到该说话人的音频片段，尝试重新生成
            ok, msg, results = _extract_speakers_audio(task_id)
            if not ok:
                return jsonify({"code": 1, "msg": f"生成音频片段失败: {msg}"}), 400
            # 再次检查指定说话人的音频片段是否存在
            segment_files = list(speaker_audio_dir.glob(f"{speaker}_seg_*.mp3"))
            if not segment_files:
                return jsonify({"code": 1, "msg": f"未找到说话人 {speaker} 的音频片段"}), 400
        
        # 查找说话人的所有音频片段
        segment_files = list(speaker_audio_dir.glob(f"{speaker}_seg_*.mp3"))
        if not segment_files:
            return jsonify({"code": 1, "msg": f"未找到说话人 {speaker} 的音频片段"}), 400
        
        # 按照索引排序
        segment_files.sort(key=lambda x: int(x.stem.split('_')[-1]))
        

        # 创建合并文件的列表
        concat_file = speaker_audio_dir / f"{speaker}_concat.txt"
        merged_file = speaker_audio_dir / f"{speaker}_extracted.mp3"
        
        # 写入concat文件
        with open(concat_file, 'w') as f:
            for seg_file in segment_files:
                f.write(f"file '{seg_file.as_posix()}'\n")
        
        # 使用ffmpeg合并音频文件并转换为MP3格式
        tools.runffmpeg(['-y', '-f', 'concat', '-safe', '0', '-i', concat_file.as_posix(), '-f', 'mp3', '-ab', '64k', merged_file.as_posix()])
        
        # 删除concat文件
        try:
            concat_file.unlink()
        except:
            pass
        
        # 检查合并后的文件是否存在
        if not merged_file.exists():
            return jsonify({"code": 1, "msg": "音频合并失败"}), 400
            
        return jsonify({"code": 0, "msg": "提取成功", "file_path": f"/apidata/{task_id}/speaker_audio/{merged_file.name}"})
        
    except Exception as e:
        return jsonify({"code": 1, "msg": f"提取异常: {str(e)}"}), 500


@voice_bp.route('/preview_merged_audio', methods=['POST'])
def vm_preview_merged_audio():
    task_id = (request.args.get('task_id') or '').strip()
    data = request.get_json(silent=True) or {}
    speaker = (data.get('speaker') or '').strip()
    segments = data.get('segments', [])  # 获取选定的片段索引
    
    if not speaker:
        return jsonify({"code": 1, "msg": "缺少说话人参数"}), 400
    
    try:
        import uuid
        
        # 获取说话人的音频片段目录
        task_dir = Path(config.ROOT_DIR) / 'apidata' / task_id
        speaker_audio_dir = task_dir / 'speaker_audio'
        
        # 检查目录是否存在
        if not speaker_audio_dir.exists():
            return jsonify({"code": 1, "msg": "说话人音频目录不存在"}), 400
        

        # 获取说话人的所有音频片段文件
        all_segment_files = list(speaker_audio_dir.glob(f"{speaker}_seg_*.mp3"))
        if not all_segment_files:
            return jsonify({"code": 1, "msg": f"未找到说话人 {speaker} 的音频片段"}), 400
        
        # 获取选定的片段文件
        segment_files = []
        if segments:
            # 根据索引过滤片段
            for index in segments:
                # 查找匹配索引的文件
                matched_files = [f for f in all_segment_files if f.name == f"{speaker}_seg_{index}.mp3"]
                if matched_files:
                    segment_files.append(matched_files[0])
                else:
                    return jsonify({"code": 1, "msg": f"未找到片段 {index}"}), 400
        else:
            # 如果没有指定片段，则使用所有片段
            segment_files = all_segment_files
        
        if not segment_files:
            return jsonify({"code": 1, "msg": "未选择任何音频片段"}), 400
        
        # 按照索引排序
        segment_files.sort(key=lambda x: int(x.stem.split('_')[-1]))
        

        # 创建合并文件的列表
        concat_file = speaker_audio_dir / f"{speaker}_preview_concat_{uuid.uuid4().hex[:8]}.txt"
        preview_file = speaker_audio_dir / f"{speaker}_preview_{uuid.uuid4().hex[:8]}.mp3"
        
        # 写入concat文件
        with open(concat_file, 'w') as f:
            for seg_file in segment_files:
                f.write(f"file '{seg_file.as_posix()}'\n")
        
        # 使用ffmpeg合并音频文件并转换为MP3格式
        tools.runffmpeg(['-y', '-f', 'concat', '-safe', '0', '-i', concat_file.as_posix(), '-f', 'mp3', '-ab', '64k', preview_file.as_posix()])
        
        # 删除concat文件
        try:
            concat_file.unlink()
        except:
            pass
        
        # 检查合并后的文件是否存在
        if not preview_file.exists():
            return jsonify({"code": 1, "msg": "音频合并失败"}), 400
            
        return jsonify({"code": 0, "msg": "预览音频生成成功", "audio_url": f"/apidata/{task_id}/speaker_audio/{preview_file.name}"})
        
    except Exception as e:
        return jsonify({"code": 1, "msg": f"生成预览音频异常: {str(e)}"}), 500


@voice_bp.route('/upload_speaker_audio', methods=['POST'])
def vm_upload_speaker_audio():
    task_id = (request.args.get('task_id') or '').strip()
    
    try:
        # 获取上传的文件
        audio_file = request.files.get('audio')
        speaker = request.form.get('speaker', '').strip()
        
        if not audio_file:
            return jsonify({"code": 1, "msg": "未找到上传的音频文件"}), 400
        
        if not speaker:
            return jsonify({"code": 1, "msg": "缺少说话人参数"}), 400
        
        # 获取任务目录
        task_dir = Path(config.ROOT_DIR) / 'apidata' / task_id
        if not task_dir.exists():
            return jsonify({"code": 1, "msg": "任务目录不存在"}), 400
        
        # 创建说话人音频目录
        speaker_audio_dir = task_dir / 'speaker_audio'
        speaker_audio_dir.mkdir(exist_ok=True)
        

        # 获取说话人现有的音频片段数量，确定新文件的索引
        existing_files = list(speaker_audio_dir.glob(f"{speaker}_seg_*.mp3"))
        next_index = len(existing_files)
        

        # 生成文件名
        filename = f"{speaker}_seg_{next_index}.mp3"
        file_path = speaker_audio_dir / filename
        
        # 保存上传的文件
        audio_file.save(file_path.as_posix())
        
        # 检查文件是否为MP3格式，如果不是则转换
        import subprocess
        try:
            # 检查文件格式
            result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_name', '-of', 'default=noprint_wrappers=1:nokey=1', file_path.as_posix()], capture_output=True, text=True)
            if result.returncode != 0:
                # 文件可能不是有效的音频文件，尝试转换
                converted_path = speaker_audio_dir / f"{speaker}_seg_{next_index}_converted.mp3"
                tools.runffmpeg(['-y', '-i', file_path.as_posix(), '-f', 'mp3', '-ab', '64k', converted_path.as_posix()])
                # 删除原始文件
                file_path.unlink()
                # 重命名转换后的文件
                converted_path.rename(file_path)
            else:
                # 文件是有效的音频文件，检查是否需要转换为MP3
                codec = result.stdout.strip()
                if codec != 'mp3':
                    # 转换为MP3格式
                    converted_path = speaker_audio_dir / f"{speaker}_seg_{next_index}_converted.mp3"
                    tools.runffmpeg(['-y', '-i', file_path.as_posix(), '-f', 'mp3', '-ab', '64k', converted_path.as_posix()])
                    # 删除原始文件
                    file_path.unlink()
                    # 重命名转换后的文件
                    converted_path.rename(file_path)
        except Exception as e:
            # 转换失败，删除文件
            if file_path.exists():
                file_path.unlink()
            return jsonify({"code": 1, "msg": f"音频文件处理失败: {str(e)}"}), 500
        
        return jsonify({"code": 0, "msg": "音频上传成功", "file_path": f"/apidata/{task_id}/speaker_audio/{filename}"})
        
    except Exception as e:
        return jsonify({"code": 1, "msg": f"上传异常: {str(e)}"}), 500
