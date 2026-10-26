const LOCAL = '本地模型（FastEmbed）';
const REMOTE = '框架嵌入提供商';
const LLM_LOCAL = 'LLM增强＋本地嵌入';
const LLM_REMOTE = 'LLM增强＋框架嵌入';
const LLM_RANK = 'LLM评分';
const REMOTE_RANK = '框架重排提供商';
const bridge = window.AstrBotPluginView || window.AstrBotPluginPage;
const $ = id => document.getElementById(id);
let state, draft, original, dirty = false, saving = false, testing = false, installing = false;
let testKind = 'embedding', previewTimer, previewSequence = 0;
const clone = value => JSON.parse(JSON.stringify(value));

function node(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined) item.textContent = text;
  return item;
}

function get(path) { return path.split('.').reduce((value, key) => value?.[key], draft); }
function set(path, value) {
  const keys = path.split('.');
  let target = draft;
  for (const key of keys.slice(0, -1)) target = target[key];
  target[keys.at(-1)] = value;
  dirty = JSON.stringify(draft) !== JSON.stringify(original);
  status();
}

function status() {
  const element = $('save-status');
  element.textContent = saving ? '正在保存…' : dirty ? '有未保存的更改' : state?.requires_restart ? '已保存 · 端口待重启' : '配置已同步';
  element.classList.toggle('dirty', dirty);
  $('save').disabled = saving || !dirty;
}

function notice(message, error = false) {
  const box = $('notice');
  box.hidden = !message;
  box.textContent = message;
  box.classList.toggle('error', error);
}

async function call(endpoint, body) {
  return body === undefined ? bridge.apiGet(endpoint) : bridge.apiPost(endpoint, body);
}

function field(path, label, {type = 'text', hint = '', options, choices, min, max, optional = false, readOnly = false} = {}) {
  const wrapper = node('label', 'field');
  wrapper.htmlFor = 'field-' + path.replaceAll('.', '-');
  wrapper.append(node('span', 'field-label', label));
  const input = node(options ? 'select' : 'input');
  input.id = wrapper.htmlFor;
  input.dataset.setting = path;
  if (options) {
    for (const option of options) {
      const element = node('option', '', option);
      element.value = option;
      input.append(element);
    }
  } else {
    input.type = type;
    if (min !== undefined) input.min = min;
    if (max !== undefined) input.max = max;
    if (type === 'number') input.step = '1';
    input.autocomplete = type === 'password' ? 'new-password' : 'off';
  }
  input.value = get(path) ?? '';
  input.readOnly = readOnly;
  input.required = !optional && type !== 'password';
  if (choices?.length && !options) {
    const datalist = node('datalist');
    datalist.id = input.id + '-options';
    for (const choice of choices) {
      const option = node('option');
      option.value = choice.id;
      datalist.append(option);
    }
    input.setAttribute('list', datalist.id);
    wrapper.append(datalist);
  }
  input.addEventListener(options ? 'change' : 'input', () => {
    set(path, type === 'number' ? (input.value === '' ? '' : Number(input.value)) : input.value);
    if (path.endsWith('.mode')) {
      renderBackends();
      renderPathsEditor();
      renderService();
      updateTestHint();
      previewPaths();
    } else if (path.startsWith('model_storage.')) {
      clearTimeout(previewTimer);
      previewTimer = setTimeout(previewPaths, 350);
    } else if (path === 'port') {
      notice(Number(input.value) !== state.listener_port ? '端口修改需要重启插件；右侧仍显示当前可用接口。' : '');
    }
  });
  wrapper.append(input);
  if (hint) wrapper.append(node('span', 'hint', hint));
  return wrapper;
}

function pair(...fields) { const row = node('div', 'field-inline'); row.append(...fields); return row; }
function advanced(title, ...fields) {
  const details = node('details', 'small-details');
  details.append(node('summary', '', title), ...fields);
  return details;
}

function providerField(path, label, kind, {optional = false, hint = ''} = {}) {
  const wrapper = node('label', 'field');
  const input = node('select'); input.id = 'field-' + path.replaceAll('.', '-'); input.dataset.setting = path;
  input.dataset.providerKind = kind; input.required = !optional;
  wrapper.htmlFor = input.id; wrapper.append(node('span', 'field-label', label));
  const empty = node('option', '', optional ? '复用重排聊天提供商' : '请选择框架中已配置的模型'); empty.value = ''; input.append(empty);
  const entries = state.providers[kind] || [];
  for (const entry of entries) {
    const option = node('option', '', entry.label); option.value = entry.id;
    option.disabled = !!entry.disabled; option.title = entry.reason || entry.location_label || '';
    input.append(option);
  }
  const selected = get(path) || '';
  if (selected && !entries.some(entry => entry.id === selected && !entry.disabled)) {
    if (!entries.some(entry => entry.id === selected)) {
      const missing = node('option', '', '未找到可用模型：' + selected); missing.value = selected; missing.disabled = true; input.append(missing);
    }
    input.setCustomValidity('请从框架已启用的正确类型模型中选择');
  }
  input.value = selected;
  input.addEventListener('change', () => {
    input.setCustomValidity(''); set(path, input.value);
    const selected = entries.find(entry => entry.id === input.value);
    if (path === 'embedding_backend.provider_id' && Number.isInteger(selected?.dimensions) && selected.dimensions > 0) {
      set('embedding_backend.dimensions', selected.dimensions); renderBackends();
    }
    renderMethodGuides(); updateTestHint();
  });
  wrapper.append(input, node('span', 'hint', hint || '来自AstrBot模型供应商页，按能力类型过滤；复用原配置与密钥。'));
  if (!entries.some(entry => !entry.disabled)) wrapper.append(node('span', 'hint missing-model', '此类型暂没有可用模型，请先在框架模型供应商页创建并启用。'));
  return wrapper;
}

function localModelField(kind) {
  const group = draft[kind + '_backend'];
  const wrapper = node('label', 'field');
  const input = node('select'); input.id = 'field-' + kind + '_backend-local_model_name'; input.dataset.setting = kind + '_backend.local_model_name'; input.dataset.localKind = kind;
  const entries = state.local_models?.[kind] || [];
  input.required = true; input.disabled = !entries.length;
  wrapper.htmlFor = input.id; wrapper.append(node('span', 'field-label', kind === 'embedding' ? '已检测到的本地嵌入模型' : '已检测到的本地重排模型'));
  const empty = node('option', '', entries.length ? '请选择已检测到的模型目录' : '未检测到可用模型，请检查下方目录'); empty.value = ''; input.append(empty);
  for (const entry of entries) {
    const option = node('option', '', entry.model + ' · ' + entry.directory); option.value = entry.key; input.append(option);
  }
  const current = entries.find(entry => entry.model === group.local_model_name && entry.directory === state.paths?.[kind + '_model_dir']);
  input.value = current?.key || '';
  input.setCustomValidity(current ? '' : '本地模型必须先检测到完整目录与文件');
  input.addEventListener('change', () => {
    const model = entries.find(entry => entry.key === input.value); if (!model) return;
    const previous = group.local_model_name;
    set(kind + '_backend.local_model_name', model.model); set('model_storage.' + kind + '_dir', model.directory);
    if (kind === 'embedding') {
      set('embedding_backend.dimensions', model.dimensions);
      if (draft.embedding_backend.api_model === previous) set('embedding_backend.api_model', model.model);
    }
    state.paths[kind + '_model_dir'] = model.directory;
    renderBackends(); previewPaths();
  });
  wrapper.append(input, node('span', 'hint', '仅列出已存在且匹配FastEmbed模型结构的目录；不会下载或创建模型。'));
  return wrapper;
}

function providerLocation(kind, id) {
  const provider = (state.providers[kind] || []).find(entry => entry.id === id);
  return provider?.location_label || '接口位置依原提供商配置';
}

function methodLocation(kind, guide) {
  const llm = providerLocation('chat', kind === 'embedding' ? (draft.embedding_backend.llm_provider_id || draft.llm_provider_id) : draft.llm_provider_id);
  const embedding = providerLocation('embedding', draft.embedding_backend.provider_id);
  const rerank = providerLocation('rerank', draft.rerank_backend.provider_id);
  return ({local:'本地CPU · 无外网模型请求', chat_provider:llm, embedding_provider:embedding, rerank_provider:rerank,
           llm_local:'本地编码 ＋ LLM：' + llm, llm_embedding_provider:'LLM：' + llm + '；嵌入：' + embedding})[guide.execution] || guide.location;
}

function renderMethodGuides() {
  for (const kind of ['embedding', 'rerank']) {
    const target = document.querySelector('[data-method-guide="' + kind + '"]');
    const methods = state.method_guides?.[kind]; if (!target || !methods) continue;
    const guide = methods[draft[kind + '_backend'].mode]; if (!guide) continue;
    target.replaceChildren(node('span', 'badge neutral', methodLocation(kind, guide)));
    const columns = node('div', 'method-tradeoffs');
    for (const [label, values] of [['优点', guide.pros], ['需要考虑', guide.cons]]) {
      const block = node('div'); block.append(node('strong', '', label)); const list = node('ul');
      for (const value of values) list.append(node('li', '', value)); block.append(list); columns.append(block);
    }
    target.append(columns);
    const details = advanced('比较全部' + (kind === 'embedding' ? '嵌入' : '重排') + '方式');
    for (const [mode, value] of Object.entries(methods)) {
      const item = node('div', 'method-comparison'); item.append(node('strong', '', mode), node('p', '', value.location),
          node('p', '', '优点：' + value.pros.join('；')), node('p', '', '代价：' + value.cons.join('；'))); details.append(item);
    }
    target.append(details);
  }
}

function renderBackends() {
  const e = draft.embedding_backend, r = draft.rerank_backend;
  const embedding = $('embedding-config');
  const rerank = $('rerank-config');
  embedding.replaceChildren(); rerank.replaceChildren();
  for (const [element, title, caption] of [[embedding, '嵌入', 'TEXT → VECTOR'], [rerank, '重排', 'QUERY × DOCUMENT']]) {
    const heading = node('div', 'backend-top');
    heading.append(node('h3', '', title), node('span', '', caption));
    element.append(heading);
  }
  embedding.append(field('embedding_backend.mode', '嵌入方式', {options: [LOCAL, REMOTE, LLM_LOCAL, LLM_REMOTE]}));
  const eg = node('div', 'method-guide'); eg.dataset.methodGuide = 'embedding'; embedding.append(eg);
  if ([LOCAL, LLM_LOCAL].includes(e.mode)) {
    embedding.append(localModelField('embedding'));
    embedding.append(pair(field('embedding_backend.dimensions', '向量维度（随模型）', {type: 'number', min: 1, max: 8192, readOnly: true}), field('embedding_threads', 'CPU线程', {type: 'number', min: 1, max: 4})));
    embedding.append(advanced('嵌入高级设置', field('embedding_backend.batch_size', '本地批量', {type: 'number', min: 1, max: 64}), field('embedding_backend.api_model', '对外模型名称')));
  } else {
    embedding.append(providerField('embedding_backend.provider_id', '框架已有嵌入模型', 'embedding'));
    embedding.append(pair(field('embedding_backend.dimensions', '真实输出维度', {type: 'number', min: 1, max: 8192}), field('embedding_backend.api_model', '对外模型名称')));
  }
  if ([LLM_LOCAL, LLM_REMOTE].includes(e.mode)) {
    embedding.append(providerField('embedding_backend.llm_provider_id', '文本整理LLM', 'chat', {optional: true, hint: '留空复用重排聊天提供商；可以选择本机或网络聊天接口。'}));
    embedding.append(node('p', 'backend-note', 'LLM先整理文本，再由真实模型生成向量。会增加调用与改写不确定性，切换后需重建索引。'));
    embedding.append(advanced('LLM整理设置', field('embedding_backend.llm_timeout_seconds', '整理总超时（秒）', {type: 'number', min: 1, max: 120}), field('embedding_backend.llm_batch_size', '每批文本数', {type: 'number', min: 1, max: 16}), field('embedding_backend.llm_max_chars', '单条字符上限', {type: 'number', min: 64, max: 16000}), field('embedding_backend.llm_cache_seconds', '整理缓存（秒）', {type: 'number', min: 0, max: 86400})));
  }
  rerank.append(field('rerank_backend.mode', '重排方式', {options: [LLM_RANK, LOCAL, REMOTE_RANK]}));
  const rg = node('div', 'method-guide'); rg.dataset.methodGuide = 'rerank'; rerank.append(rg);
  if (r.mode === LLM_RANK) {
    rerank.append(providerField('llm_provider_id', '框架已有聊天模型', 'chat', {hint: '复用框架中的DS4.1F或其他聊天模型。'}));
    rerank.append(pair(field('max_rerank_documents', '候选上限', {type: 'number', min: 1, max: 64}), field('rerank_timeout_seconds', '总超时（秒）', {type: 'number', min: 1, max: 120})));
    rerank.append(node('p', 'backend-note', '建议8–16条候选。框架与记忆插件的外层超时，需要大于这里的总超时。'));
  } else if (r.mode === LOCAL) {
    rerank.append(localModelField('rerank'));
    rerank.append(pair(field('rerank_backend.threads', 'CPU线程', {type: 'number', min: 1, max: 4}), field('rerank_backend.batch_size', '本地批量', {type: 'number', min: 1, max: 32})));
    const checkbox = node('label', 'checkbox-field');
    const input = node('input'); input.type = 'checkbox'; input.checked = r.sigmoid; input.dataset.setting = 'rerank_backend.sigmoid';
    input.addEventListener('change', () => set('rerank_backend.sigmoid', input.checked));
    checkbox.append(input, node('span', '', '分数映射到0–1（不改变排序）')); rerank.append(checkbox);
  } else {
    rerank.append(providerField('rerank_backend.provider_id', '框架已有重排模型', 'rerank'));
  }
  rerank.append(advanced('对外名称与评分设置', field('rerank_backend.api_model', '对外模型名称'), field('max_document_chars', '每条候选字符上限', {type: 'number', min: 64, max: 4096}), ...(r.mode === LLM_RANK ? [field('rerank_cache_seconds', '评分缓存（秒）', {type: 'number', min: 0, max: 3600})] : [])));
  renderMethodGuides();
}

function renderPathsEditor() {
  const container = $('paths-editor');
  container.replaceChildren(field('model_storage.root_dir', '统一模型根目录', {hint: '相对插件数据目录，默认为fastembed；也可填写绝对路径。'}));
  const localEmbedding = [LOCAL, LLM_LOCAL].includes(draft.embedding_backend.mode);
  const localRerank = draft.rerank_backend.mode === LOCAL;
  const overrides = [];
  if (localEmbedding) overrides.push(field('model_storage.embedding_dir', '嵌入目录覆盖', {optional: true, hint: '留空自动识别；设置后严格使用该路径。'}));
  if (localRerank) overrides.push(field('model_storage.rerank_dir', '重排目录覆盖', {optional: true, hint: '兼容插件安装目录中的fastembed/MiniLM-L-6-v2。'}));
  if (overrides.length) container.append(advanced('手动覆盖模型路径', ...overrides));
}

function renderPaths(paths) {
  const container = $('paths-output'); container.replaceChildren();
  container.append(node('p', 'path-subtitle', '插件数据：' + paths.data_dir));
  for (const [kind, label] of [['embedding', '嵌入模型'], ['rerank', '重排模型']]) {
    const row = node('div', 'path-line');
    const body = node('div');
    const value = node('output', 'path-value', paths[kind + '_model_dir']);
    value.dataset.path = kind;
    const meta = node('div', 'path-meta');
    const present = paths[kind + '_files_present'];
    meta.append(node('span', present ? 'present' : 'missing', present ? '● 默认模型文件齐全' : '○ 请检查目录与模型文件'), node('span', '', paths[kind + '_origin']));
    body.append(value, meta); row.append(node('strong', '', label), body); container.append(row);
  }
}

async function previewPaths() {
  const sequence = ++previewSequence;
  try {
    const result = await call('settings/preview', {settings: clone(draft)});
    if (sequence === previewSequence) {
      state.paths = result.paths; state.local_models = result.local_models; state.runtime = result.runtime;
      renderPaths(result.paths); renderBackends(); renderRuntime();
    }
  } catch (error) {
    if (sequence === previewSequence) notice(error.message || '路径预览失败', true);
  }
}

function renderRuntime() {
  const runtime = state.runtime; if (!runtime) return;
  $('runtime-status').textContent = runtime.message;
  $('runtime-packages').textContent = runtime.packages.map(p => p.name + ': ' + (p.present ? (p.version || '已发现模块') : '缺失')).join(' · ');
  $('runtime-install-note').textContent = runtime.automatic_install;
  $('install-runtime').disabled = installing || runtime.installing;
  $('install-runtime').textContent = installing || runtime.installing ? '正在安装运行库…' : '安装/修复运行库';
}

async function installRuntime() {
  if (installing) return;
  installing = true; renderRuntime(); notice('正在使用AstrBot安装器处理插件requirements.txt，进度请查看框架日志。');
  try {
    const result = await call('settings/dependencies', {}); state.runtime = result.runtime; state.local_models = result.local_models;
    renderBackends(); notice(result.runtime.message); await previewPaths();
  } catch (error) {notice(error.message || '依赖安装失败，请查看框架日志', true);}
  finally {installing = false; renderRuntime();}
}

function renderService() {
  const area = $('service-config'); area.replaceChildren();
  area.append(field('port', '本机端口', {type: 'number', min: 1024, max: 65535, hint: '修改端口需重启插件；其他设置可直接应用。'}), field('api_key', '服务访问密钥', {type: 'password', hint: '修改后，框架提供商需同步更新密钥。'}), field('embedding_timeout_seconds', '嵌入总超时（秒）', {type: 'number', min: 1, max: 180}));
  if (draft.rerank_backend.mode !== LLM_RANK) area.append(field('rerank_timeout_seconds', '重排总超时（秒）', {type: 'number', min: 1, max: 120}), field('max_rerank_documents', '候选数量上限', {type: 'number', min: 1, max: 64}));
}

async function copyText(value, input) {
  try { await navigator.clipboard.writeText(String(value)); notice('已复制'); }
  catch (_) {
    if (input) { input.focus(); input.select(); }
    else {
      const textarea = node('textarea'); textarea.value = String(value); document.body.append(textarea); textarea.select();
      const copied = document.execCommand('copy'); textarea.remove(); notice(copied ? '已复制' : '当前环境不支持自动复制，请选中文本后按Ctrl+C'); return;
    }
    notice(document.execCommand('copy') ? '已复制' : '已选中文本，请按Ctrl+C复制');
  }
}

function renderConnections() {
  const container = $('connections'); container.replaceChildren();
  const titles = {openai_embedding_fields: 'OpenAI Embedding', vllm_rerank_fields: 'vLLM Rerank'};
  const labels = {provider_type:'提供商类型',provider_id:'建议提供商ID',embedding_api_base:'API Base',rerank_api_base:'API Base',endpoint:'完整接口',embedding_api_key:'API Key',rerank_api_key:'API Key',embedding_model:'模型名称',rerank_model:'模型名称',embedding_dimensions:'向量维度',embedding_dimensions_mode:'dimensions发送模式',timeout:'提供商超时（秒）',proxy:'代理',custom_headers:'自定义请求头',encoding_format:'支持的编码',rerank_api_suffix:'API后缀',top_n:'返回条数 top_n'};
  for (const [name, title] of Object.entries(titles)) {
    const values = state.connections[name];
    const details = node('details', 'connection'); details.open = true; details.append(node('summary', '', title));
    const content = node('div', 'connection-content');
    content.append(node('p', 'connection-help', name.startsWith('openai') ? '复制到框架的嵌入提供商，模型维度需要一致。' : '这是协议适配器名称，无需安装vLLM。'));
    const extra = advanced('其他对接参数');
    const mainKeys = new Set(['provider_id', 'embedding_api_base', 'rerank_api_base', 'embedding_api_key', 'rerank_api_key', 'embedding_model', 'rerank_model', 'embedding_dimensions', 'rerank_api_suffix', 'timeout']);
    for (const [key, value] of Object.entries(values)) {
      const wrapper = node('label', 'field'); wrapper.append(node('span', 'field-label', labels[key] || key));
      const row = node('div', 'copy-row');
      const input = node('input'); input.readOnly = true; input.value = value === '' ? '' : String(value); input.placeholder = '留空'; input.dataset.connection = name + '.' + key;
      const secret = key.endsWith('api_key'); input.type = secret ? 'password' : 'text'; input.setAttribute('aria-label', title + ' ' + (labels[key] || key));
      row.append(input);
      if (secret) {
        const show = node('button', 'copy-button', '显示'); show.type = 'button';
        show.addEventListener('click', () => { input.type = input.type === 'password' ? 'text' : 'password'; show.textContent = input.type === 'password' ? '显示' : '隐藏'; }); row.append(show);
      }
      const copy = node('button', 'copy-button', '复制'); copy.type = 'button'; copy.setAttribute('aria-label', '复制' + title + ' ' + (labels[key] || key)); copy.addEventListener('click', () => copyText(value, input)); row.append(copy);
      wrapper.append(row); (mainKeys.has(key) ? content : extra).append(wrapper);
    }
    content.append(extra);
    const all = node('button', 'button copy-all', '复制整组参数 JSON'); all.type = 'button'; all.addEventListener('click', () => copyText(JSON.stringify(values, null, 2)));
    content.append(all); details.append(content); container.append(details);
  }
}

function applyPatch(target, patch) {
  for (const [key, value] of Object.entries(patch)) {
    if (value && typeof value === 'object' && !Array.isArray(value)) applyPatch(target[key], value);
    else target[key] = value;
  }
}

function renderPresets() {
  const area = $('presets'); area.replaceChildren();
  for (const preset of state.recommendations) {
    const button = node('button', 'preset'); button.type = 'button'; button.dataset.preset = preset.id;
    button.append(node('strong', '', preset.title), node('p', '', preset.description), node('span', 'badge', preset.badge));
    button.addEventListener('click', () => {
      applyPatch(draft, clone(preset.patch)); dirty = JSON.stringify(draft) !== JSON.stringify(original);
      renderBackends(); renderPathsEditor(); renderService(); updateTestHint(); status(); previewPaths();
      notice('已填入「' + preset.title + '」；请测试后保存。');
    });
    area.append(button);
  }
}

function renderTestInputs() {
  const area = $('test-inputs'); area.replaceChildren();
  $('test-embedding-tab').setAttribute('aria-pressed', String(testKind === 'embedding'));
  $('test-rerank-tab').setAttribute('aria-pressed', String(testKind === 'rerank'));
  if (testKind === 'embedding') {
    const label = node('label', 'field'); label.append(node('span', 'field-label', '测试文本（每行一条，最多8条）'));
    const input = node('textarea'); input.id = 'embedding-texts'; input.rows = 3; input.value = '我喜欢喝绿茶\n他爱喝茶\n服务器磁盘空间不足'; label.append(input); area.append(label);
  } else {
    const q = node('label', 'field'); q.append(node('span', 'field-label', '查询问题')); const input = node('input'); input.id = 'rerank-query'; input.value = '用户喜欢喝什么饮料？'; q.append(input);
    const d = node('label', 'field'); d.append(node('span', 'field-label', '候选文本（每行一条，最多16条）')); const docs = node('textarea'); docs.id = 'rerank-documents'; docs.rows = 3; docs.value = '用户喜欢喝绿茶\n服务器磁盘空间不足\n用户喜欢跑步'; d.append(docs); area.append(q, d);
  }
  $('run-test').textContent = testKind === 'embedding' ? '测试嵌入' : '测试重排'; updateTestHint();
}

function updateTestHint() {
  if (!draft) return;
  const mode = draft[testKind + '_backend'].mode;
  const guide = state.method_guides?.[testKind]?.[mode];
  $('test-call-hint').textContent = mode === LOCAL ? '仅在本机执行，不调用付费LLM。' :
      '将调用框架原有提供商（' + (guide ? methodLocation(testKind, guide) : '位置依提供商配置') + '），费用与后续转发由原配置决定。';
}

function renderResult(result) {
  const area = $('test-result'); area.classList.remove('error'); area.replaceChildren();
  const heading = node('div', 'result-head'); heading.append(node('strong', '', '测试成功'), node('span', 'badge neutral', result.configuration), node('span', 'muted', result.elapsed_ms + ' ms')); area.append(heading);
  if (result.kind === 'embedding') {
    const grid = node('div', 'result-grid');
    for (const [value, label] of [[result.count, '条文本'], [result.dimensions[0], '维向量'], [Number(result.norms[0]).toFixed(4), '首条向量范数']]) {
      const item = node('div', 'metric'); item.append(node('strong', '', value), node('span', '', label)); grid.append(item);
    }
    area.append(grid, node('pre', '', '余弦相似度矩阵\n' + result.cosine_similarity.map(row => row.map(value => value.toFixed(4)).join('    ')).join('\n')));
  } else {
    for (const [rank, row] of result.results.entries()) {
      const line = node('div', 'result-row'); line.append(node('span', '', '#' + (rank + 1) + ' · ' + Number(row.relevance_score).toFixed(4)), node('div', '', row.text)); area.append(line);
    }
  }
}

async function runTest() {
  if (testing || saving || !$('configuration').reportValidity()) return;
  testing = true; $('run-test').disabled = true; $('test-embedding-tab').disabled = true; $('test-rerank-tab').disabled = true;
  const area = $('test-result'); area.classList.remove('error'); area.replaceChildren(node('div', 'empty-result', '正在执行真实模型请求，请稍候…'));
  const payload = {kind: testKind, settings: clone(draft)};
  if (testKind === 'embedding') payload.texts = $('embedding-texts').value.split(/\r?\n/).filter(text => text.trim());
  else {payload.query = $('rerank-query').value; payload.documents = $('rerank-documents').value.split(/\r?\n/).filter(text => text.trim());}
  try { renderResult(await call('settings/test', payload)); }
  catch (error) { area.classList.add('error'); area.replaceChildren(node('strong', '', '测试未通过'), node('p', '', error.message || '模型调用失败，请检查提供商与模型目录。')); }
  finally {testing = false; $('run-test').disabled = false; $('test-embedding-tab').disabled = false; $('test-rerank-tab').disabled = false;}
}

function render(snapshot) {
  state = snapshot; draft = clone(snapshot.settings); original = clone(draft); dirty = false;
  $('workspace').hidden = false; renderPresets(); renderBackends(); renderPathsEditor(); renderPaths(snapshot.paths); renderRuntime(); renderService(); renderConnections(); renderTestInputs(); status();
  if (state.requires_restart) notice('设置已保存；端口变更需要重启插件，右侧仍展示当前运行地址。');
}

async function save() {
  if (saving || testing || !$('configuration').reportValidity()) return;
  saving = true; status();
  document.querySelectorAll('#configuration input, #configuration select, #presets button').forEach(el => el.disabled = true);
  try {const result = await call('settings/save', {revision: state.revision, settings: clone(draft)}); render(result); notice(result.message);}
  catch (error) {notice(error.message || '保存失败', true);}
  finally {
    saving = false;
    document.querySelectorAll('#configuration input, #configuration select, #presets button').forEach(el => {
      el.disabled = !!el.dataset.localKind && !(state.local_models?.[el.dataset.localKind] || []).length;
    });
    status();
  }
}

$('save').addEventListener('click', save);
$('reload').addEventListener('click', async () => {try {render(await call('settings')); notice('已重新读取保存的配置。');} catch (error) {notice(error.message, true);}});
$('run-test').addEventListener('click', runTest);
$('refresh-models').addEventListener('click', previewPaths);
$('install-runtime').addEventListener('click', installRuntime);
$('test-embedding-tab').addEventListener('click', () => {testKind = 'embedding'; renderTestInputs();});
$('test-rerank-tab').addEventListener('click', () => {testKind = 'rerank'; renderTestInputs();});
$('configuration').addEventListener('submit', event => event.preventDefault());

try {
  if (!bridge) throw new Error('请从AstrBot插件详情的「重排和嵌入」视图打开本页。');
  const context = await bridge.ready();
  render(await call('settings'));
  if (context?.displayName?.includes('本地验证')) notice('本地验证环境：本地模型执行真实推理，聊天提供商使用测试替身。');
} catch (error) {
  $('fatal').hidden = false; $('fatal').textContent = error.message || '无法读取插件状态'; $('save-status').textContent = '连接失败';
}
