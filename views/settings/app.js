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
let currentRoute = 'backends', connectionLoading = false, connectionAction = '';
const testDrafts = {embedding: {texts: '我喜欢喝绿茶\n他爱喝茶\n服务器磁盘空间不足'},
  rerank: {query: '用户喜欢喝什么饮料？', documents: '用户喜欢喝绿茶\n服务器磁盘空间不足\n用户喜欢跑步'}};
const clone = value => JSON.parse(JSON.stringify(value));
const pages = {
  backends: ['模型后端', '配置你的检索能力', '让嵌入与重排各司其职，选择适合当前服务的运行方式。'],
  testing: ['推理测试', '让每个结果都有依据', '使用真实推理验证当前配置，分别检查嵌入与重排的输出。'],
  'local-data': ['本地数据', '管理本地模型与路径', '查看文件实际位置，使用现有目录中的模型。'],
  debug: ['Debug 与运行库', '了解服务的运行状态', '集中查看环境、模型状态与问题原因，按提示逐步处理。'],
  'connections/embedding': ['对接参数 / 嵌入', 'OpenAI Embedding 对接', '查看当前接口参数，一键同步到 AstrBot 项目的嵌入提供商。'],
  'connections/rerank': ['对接参数 / 重排', 'vLLM Rerank 对接', '查看当前接口参数，一键同步到 AstrBot 项目的重排提供商。'],
};

function icon(name) {
  const paths = {
    layers: ['m12 3 9 5-9 5-9-5 9-5Z', 'm3 12 9 5 9-5', 'm3 16 9 5 9-5'],
    play: ['m8 5 11 7-11 7V5Z'], folder: ['M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10H3V7Z'],
    pulse: ['M3 12h4l3-8 4 16 3-8h4'], link: ['m10 13 4-4', 'M8 16H6a4 4 0 0 1 0-8h4', 'M14 8h4a4 4 0 0 1 0 8h-4'],
    refresh: ['M20 7v5h-5', 'M4 17v-5h5', 'M6 7a7 7 0 0 1 12-1l2 3', 'm4 15 2 3a7 7 0 0 0 12-1'],
    check: ['m5 12 4 4L19 6'], arrow: ['M4 12h16', 'm14 6 6 6-6 6'], info: ['M12 11v6', 'M12 7h.01'],
    download: ['M12 3v12', 'm7 10 5 5 5-5', 'M4 16v4h16v-4'], spark: ['m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z'],
    alert: ['m12 3 10 18H2L12 3Z', 'M12 9v5', 'M12 17h.01'], trash: ['M4 6h16', 'M9 6V3h6v3', 'm6 6 1 15h10l1-15', 'M10 10v7', 'M14 10v7'],
  };
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  for (const [key, value] of Object.entries({viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.6', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true'})) svg.setAttribute(key, value);
  if (name === 'info') {const circle = document.createElementNS(svg.namespaceURI, 'circle'); circle.setAttribute('cx', 12); circle.setAttribute('cy', 12); circle.setAttribute('r', 9); svg.append(circle);}
  for (const value of paths[name] || paths.layers) {const path = document.createElementNS(svg.namespaceURI, 'path'); path.setAttribute('d', value); svg.append(path);}
  return svg;
}

function hydrateIcons() {document.querySelectorAll('[data-icon]').forEach(element => {if (!element.childElementCount) element.append(icon(element.dataset.icon));});}

function navigate(route, {replace = false, scroll = true} = {}) {
  currentRoute = pages[route] ? route : 'backends';
  document.querySelectorAll('[data-page]').forEach(page => {page.hidden = page.dataset.page !== currentRoute;});
  document.querySelectorAll('.navigation [data-route]').forEach(button => {
    const active = button.dataset.route === currentRoute; button.classList.toggle('active', active);
    if (active) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current');
  });
  const connections = currentRoute.startsWith('connections/');
  $('connections-menu').classList.toggle('active', connections); $('connections-menu').setAttribute('aria-expanded', String(connections));
  $('connections-submenu').hidden = !connections;
  const [breadcrumb, title, description] = pages[currentRoute];
  $('page-breadcrumb').textContent = breadcrumb; $('page-title').textContent = title; $('page-description').textContent = description;
  document.title = breadcrumb + ' · Retrieval Bridge';
  if (location.hash.slice(1) !== currentRoute) history[replace ? 'replaceState' : 'pushState'](null, '', '#' + currentRoute);
  if (scroll) window.scrollTo({top: 0, behavior: 'instant'});
  if (connections && state) refreshConnections();
}

function validateConfiguration(kind) {
  const relevant = control => {
    if (!kind) return true;
    const path = control.dataset.setting || '';
    if (path.startsWith(kind + '_backend.') || path.startsWith('model_storage.') || path === 'api_key' || path === 'port') return true;
    return kind === 'embedding' ? path.startsWith('embedding_') || (path === 'llm_provider_id' && draft.embedding_backend.mode === LLM_LOCAL && !draft.embedding_backend.llm_provider_id) :
      ['llm_provider_id', 'max_rerank_documents', 'max_document_chars', 'rerank_timeout_seconds', 'rerank_cache_seconds'].includes(path);
  };
  const invalid = [...document.querySelectorAll('#configuration [data-setting]')].find(control => relevant(control) && control.willValidate && !control.checkValidity());
  if (!invalid) return true;
  const page = invalid.closest('[data-page]'); if (page) navigate(page.dataset.page);
  let ancestor = invalid.parentElement;
  while (ancestor && ancestor !== $('configuration')) {if (ancestor.tagName === 'DETAILS') ancestor.open = true; ancestor = ancestor.parentElement;}
  const label = invalid.closest('.field')?.querySelector('.field-label')?.textContent || '配置项';
  notice('请先完成「' + label + '」的设置。', true);
  invalid.focus(); invalid.reportValidity(); return false;
}

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
  $('save').disabled = saving || !!connectionAction || !dirty;
  $('reload').disabled = saving || !!connectionAction;
  if (state) {
    document.querySelectorAll('.active-config-note').forEach(note => {note.hidden = !dirty;});
  }
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
      notice(Number(input.value) !== state.listener_port ? '端口修改需要重启插件；对接参数页仍显示当前运行地址。' : '');
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

function localDirectoryLabel(entry) {
  if (entry.label && !entry.label.startsWith(entry.model + ' · ') && !/^(?:[A-Za-z]:[\\/]|[\\/])/.test(entry.label)) return entry.label;
  const directory = String(entry.directory || '').replaceAll('\\', '/').replace(/\/$/, '');
  const root = String(state.paths?.model_root || '').replaceAll('\\', '/').replace(/\/$/, '');
  let parts;
  if (root && (directory.toLowerCase() === root.toLowerCase() || directory.toLowerCase().startsWith(root.toLowerCase() + '/'))) {
    parts = [root.split('/').at(-1), ...directory.slice(root.length).split('/').filter(Boolean)];
  } else {
    parts = directory.split('/').filter(Boolean);
    const fastembed = parts.findLastIndex(part => part.toLowerCase() === 'fastembed');
    if (fastembed >= 0) parts = parts.slice(fastembed);
    else {
      const snapshot = parts.indexOf('snapshots');
      parts = snapshot > 0 ? parts.slice(snapshot - 1, snapshot) : parts.slice(-1);
    }
  }
  const snapshot = parts.indexOf('snapshots');
  if (snapshot >= 0) {
    parts = parts.slice(0, snapshot);
    if (parts.length > 2 && parts.at(-1).startsWith('models--')) parts.pop();
  }
  return parts.join('\\') || '未命名目录';
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
    const option = node('option', '', localDirectoryLabel(entry)); option.value = entry.key; input.append(option);
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
  wrapper.append(input, node('span', 'hint', '仅列出已检测到的模型目录；完整路径可在“本地数据”页查看。'));
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
      for (const value of values || []) list.append(node('li', '', value)); block.append(list); columns.append(block);
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
      state.paths = result.paths; state.local_models = result.local_models; state.runtime = result.runtime; state.model_status = result.model_status; state.diagnostics = result.diagnostics;
      renderPaths(result.paths); renderBackends(); renderRuntime();
    }
  } catch (error) {
    if (sequence === previewSequence) notice(error.message || '路径预览失败', true);
  }
}

function modelLabel(status) {
  return status?.label || ({ready: '已就绪', untested: '待测试', files_missing: '检查模型文件', runtime_error: '运行库异常', failed: '测试未通过'})[status?.state] || '待测试';
}

function diagnosticState() {
  if (state.diagnostics) return state.diagnostics;
  const runtime = state.runtime || {packages: [], available: false};
  const issues = [], seen = new Set();
  for (const [kind, capability] of Object.entries(runtime.capabilities || {})) {
    if (capability.available) continue;
    const key = capability.message.replace(/的嵌入接口|的重排接口/g, '的模型接口');
    const existing = issues.find(issue => issue.details === key);
    if (existing) {existing.kinds.push(kind); continue;}
    if (seen.has(key)) continue; seen.add(key);
    issues.push({id: 'runtime-' + issues.length, title: '本地运行库无法加载', summary: '依赖文件已经检测到，当前进程仍无法导入模型接口。', details: key, kinds: [kind], suggestions: []});
  }
  const models = {};
  for (const kind of ['embedding', 'rerank']) {
    const status = state.model_status?.[kind] || {state: 'untested', message: '请运行一次推理测试。'};
    models[kind] = {...status, label: modelLabel(status), message: status.state === 'runtime_error' ? '本地运行库尚未可用，请先处理下方环境问题。' : status.message};
  }
  return {state: runtime.available ? 'healthy' : 'error', title: runtime.available ? '运行库可以正常导入' : '运行环境需要处理',
    summary: runtime.available ? '嵌入与重排的模型可用性，请分别通过推理测试确认。' : '已将影响多个模型的相同原因合并展示。', issues, models, packages: runtime.packages, environment: {}};
}

function statusTone(status) {return status?.state === 'ready' ? '' : ['runtime_error', 'failed'].includes(status?.state) ? 'error' : 'neutral';}

function renderStatusStrip(diagnostics) {
  const area = $('status-strip'); area.replaceChildren();
  for (const kind of ['embedding', 'rerank']) {
    const status = diagnostics.models?.[kind] || state.model_status?.[kind] || {};
    const card = node('div', 'status-tile'); const symbol = node('span', 'tile-icon'); symbol.append(icon(kind === 'embedding' ? 'layers' : 'spark'));
    const text = node('div'); text.append(node('strong', '', kind === 'embedding' ? '嵌入模型' : '重排模型'), node('span', 'tile-meta', draft[kind + '_backend'].mode));
    const label = node('span', 'status-label'); label.append(node('i', 'status-dot ' + status.state), document.createTextNode(modelLabel(status)));
    card.append(symbol, text, label); area.append(card);
  }
  const healthy = !!state.runtime?.available;
  const runtime = node('button', 'status-tile'); runtime.type = 'button'; runtime.setAttribute('aria-label', '查看 Debug 运行环境诊断'); const symbol = node('span', 'tile-icon'); symbol.append(icon('pulse'));
  const text = node('div'); text.append(node('strong', '', '运行环境'), node('span', 'tile-meta', (diagnostics.issues?.length || 0) ? diagnostics.issues.length + ' 个待处理问题' : 'Python · CPU Runtime'));
  const label = node('span', 'status-label'); label.append(node('i', 'status-dot ' + (healthy ? 'healthy' : 'error')), document.createTextNode(healthy ? '可导入' : '需处理'));
  runtime.append(symbol, text, label); runtime.addEventListener('click', () => navigate('debug')); area.append(runtime);
}

function renderRuntime() {
  const runtime = state.runtime; if (!runtime) return;
  const diagnostics = diagnosticState(); renderStatusStrip(diagnostics);
  const issues = diagnostics.issues || [];
  $('debug-count').hidden = !issues.length; $('debug-count').textContent = issues.length;
  const summary = $('debug-summary'); summary.replaceChildren(); summary.classList.toggle('error', diagnostics.state === 'error'); summary.classList.toggle('warning', diagnostics.state !== 'error' && issues.length > 0);
  const symbol = node('span'); symbol.append(icon(issues.length ? 'alert' : 'check'));
  const copy = node('div'); copy.append(node('strong', '', diagnostics.title), node('p', '', diagnostics.summary)); summary.append(symbol, copy);
  const models = $('model-readiness'); models.replaceChildren();
  for (const kind of ['embedding', 'rerank']) {
    const status = diagnostics.models?.[kind] || state.model_status?.[kind] || {state: 'untested'};
    const card = node('div', 'model-state-card'); card.dataset.modelStatus = kind; card.dataset.state = status.state;
    const top = node('div', 'model-state-top'); top.append(node('strong', '', kind === 'embedding' ? '嵌入模型' : '重排模型'), node('span', 'badge ' + statusTone(status), modelLabel(status)));
    const message = status.state === 'runtime_error' ? '需要先修复本地运行环境，再进行模型测试。' : status.message || '运行一次推理测试，验证当前配置。';
    card.append(top, node('p', '', message)); models.append(card);
  }
  const details = $('diagnostic-issues'); details.replaceChildren();
  for (const issue of issues) {
    const card = node('article', 'diagnostic-card'); card.dataset.diagnosticId = issue.id;
    const localIssue = !String(issue.id).startsWith('model-');
    const heading = node('div', 'issue-heading'); heading.append(node('h3', '', issue.title), node('span', 'badge warning', (issue.kinds || []).map(kind => (localIssue ? '本地' : '') + (kind === 'embedding' ? '嵌入' : '重排')).join(' / ') || '环境'));
    card.append(heading, node('p', 'issue-summary', issue.summary));
    if (issue.suggestions?.length) {
      const suggestions = node('div', 'issue-suggestions'); suggestions.append(node('strong', '', '建议处理顺序')); const list = node('ul');
      for (const suggestion of issue.suggestions) list.append(node('li', '', suggestion)); suggestions.append(list); card.append(suggestions);
    }
    if (issue.details) {const technical = node('details', 'technical-details'); technical.append(node('summary', '', '展开技术详情'), node('pre', '', Array.isArray(issue.details) ? issue.details.join('\n\n') : issue.details)); card.append(technical);}
    if (issue.links?.length) {
      const links = node('div', 'diagnostic-links');
      for (const reference of issue.links) {
        try {if (!['https:', 'http:'].includes(new URL(reference.url).protocol)) continue;} catch (_) {continue;}
        const link = node('a', '', reference.label); link.href = reference.url; link.target = '_blank'; link.rel = 'noopener noreferrer'; links.append(link);
      }
      card.append(links);
    }
    details.append(card);
  }
  $('runtime-status').textContent = runtime.available ? '可正常导入' : '需要处理'; $('runtime-status').className = 'badge ' + (runtime.available ? '' : 'warning');
  const packages = $('runtime-packages'); packages.replaceChildren();
  for (const item of diagnostics.packages || runtime.packages || []) {
    const card = node('div', 'package-chip'); card.append(node('strong', '', item.name), node('span', item.present ? '' : 'missing', item.present ? item.version || '已发现' : '未安装')); packages.append(card);
  }
  const environment = $('runtime-environment'); environment.replaceChildren();
  if (diagnostics.environment && Object.values(diagnostics.environment).some(Boolean)) {
    const fold = advanced('查看当前 Python 环境');
    for (const [key, label] of [['python', 'Python'], ['bits', '进程位数'], ['platform', '系统'], ['executable', '解释器']]) {
      if (!diagnostics.environment[key]) continue;
      const row = node('div', 'environment-row'); row.append(node('span', '', label), node('code', '', diagnostics.environment[key])); fold.append(row);
    }
    environment.append(fold);
  }
  $('runtime-details').replaceChildren();
  $('runtime-install-note').textContent = '通过 AstrBot 安装器处理本插件的 requirements.txt。' + (issues.some(issue => issue.id === 'onnxruntime-dll') ? 'Windows 系统动态库需按上方诊断单独处理。' : '安装完成后刷新状态，再执行模型测试。');
  $('install-runtime').disabled = installing || runtime.installing;
  $('install-runtime').textContent = installing || runtime.installing ? '正在处理 Python 依赖…' : '安装/修复 Python 依赖';
}

async function installRuntime() {
  if (installing) return;
  installing = true; renderRuntime(); notice('正在使用 AstrBot 安装器处理 Python 依赖，进度请查看框架日志。');
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
  const management = state.connections_management || {};
  const labels = {type:'适配器类型',provider_type:'能力类型',provider_id:'提供商 ID',embedding_api_base:'API Base',rerank_api_base:'API Base',endpoint:'完整接口',embedding_api_key:'API Key',rerank_api_key:'API Key',embedding_model:'模型名称',rerank_model:'模型名称',embedding_dimensions:'向量维度',embedding_dimensions_mode:'dimensions 发送模式',timeout:'提供商超时（秒）',proxy:'代理',custom_headers:'自定义请求头',encoding_format:'支持的编码',rerank_api_suffix:'API 后缀',top_n:'返回条数 top_n',origin_plugin:'来源插件 ID',origin_plugin_name:'来源插件名称'};
  for (const [kind, name, title, protocol] of [['embedding', 'openai_embedding_fields', '嵌入接口', 'OPENAI EMBEDDING'], ['rerank', 'vllm_rerank_fields', '重排接口', 'VLLM RERANK']]) {
    const container = $('connection-' + kind); container.replaceChildren(); container.dataset.connectionKind = kind;
    const values = state.connections?.[name] || {};
    const registered = management.providers?.[kind] || {};
    const panel = node('section', 'panel'); const heading = node('div', 'connection-intro');
    const titleArea = node('div'); titleArea.append(node('span', 'section-kicker', 'PROJECT CONNECTION'), node('h2', '', title));
    heading.append(titleArea, node('span', 'protocol-mark', protocol)); panel.append(heading);
    const origin = node('p', 'plugin-origin'); origin.append(icon('layers'), node('span', '', '来自插件 ' + (management.origin?.plugin_id || 'astrbot_plugin_llm_retrieval_bridge') + ' · ' + (management.origin?.display_name || 'LLM重排与本地嵌入桥接'))); panel.append(origin);
    const statusNames = {missing:'尚未同步',absent:'尚未同步',not_synced:'尚未同步',synced:'已同步',managed:'本插件管理',outdated:'待更新',changed:'待更新',conflict:'ID 冲突',unavailable:'API 不可用',unsupported:'API 不可用',external:'非本插件管理'};
    const statusText = connectionLoading ? '正在读取' : statusNames[registered.status] || (registered.managed ? '本插件管理' : '尚未同步');
    const registration = node('div', 'project-registration'); const top = node('div', 'registration-top');
    top.append(node('strong', '', 'AstrBot 项目提供商'), node('span', 'badge ' + (registered.status === 'conflict' ? 'error' : registered.managed ? '' : 'neutral'), statusText));
    const meta = node('div', 'registration-meta'); meta.append(node('span', '', '提供商 ID：' + (registered.provider_id || values.provider_id || '—')),
      node('span', '', '当前模型：' + (registered.registered?.model || values.embedding_model || values.rerank_model || '—')));
    if (registered.registered) meta.append(node('span', '', '项目状态：' + (registered.registered.enabled ? '已启用' : '已停用') + ' · ' + (registered.registered.loaded ? '已加载' : '未加载')));
    registration.append(top, meta);
    if (registered.message || management.api?.available === false) registration.append(node('p', 'registration-note', registered.message || '当前框架未提供可用的提供商管理 API，可手动复制下方参数。'));
    panel.append(registration);
    const actions = node('div', 'connection-actions');
    const sync = node('button', 'button primary'); sync.type = 'button'; sync.dataset.connectionAction = 'sync'; sync.dataset.kind = kind;
    const remove = node('button', 'button danger'); remove.type = 'button'; remove.dataset.connectionAction = 'remove'; remove.dataset.kind = kind;
    const inProgress = !!connectionAction;
    sync.disabled = inProgress || connectionLoading || saving || registered.can_sync !== true;
    remove.disabled = inProgress || connectionLoading || saving || registered.can_remove !== true;
    sync.append(icon('download'), document.createTextNode(connectionAction === 'sync:' + kind ? '正在同步…' : registered.managed ? '更新项目配置' : '同步到项目'));
    remove.append(icon('trash'), document.createTextNode(connectionAction === 'remove:' + kind ? '正在删除…' : '从项目删除'));
    sync.addEventListener('click', () => manageConnection('sync', kind)); remove.addEventListener('click', () => manageConnection('remove', kind));
    actions.append(sync, remove, node('span', 'action-note', '通过框架 API 管理本插件的提供商'));
    panel.append(actions);
    const pending = node('p', 'active-config-note', '表单有未保存的更改；同步使用当前已应用的参数。'); pending.hidden = !dirty; panel.append(pending);
    container.append(panel);
    const content = node('section', 'panel'); const parametersHead = node('div', 'section-head');
    const parametersTitle = node('div'); parametersTitle.append(node('span', 'section-kicker', 'ACTIVE PARAMETERS'), node('h2', '', '当前可用的对接参数'));
    parametersHead.append(parametersTitle, node('span', 'badge neutral', '只读 · 可复制')); content.append(parametersHead);
    content.append(node('p', 'connection-help', kind === 'embedding' ? '兼容 OpenAI Embeddings 协议，使用此处的模型名称和真实输出维度。' : '兼容 vLLM Rerank 协议；此名称表示接口适配方式。'));
    const fields = node('div', 'connection-parameters'); const extra = advanced('其他对接参数'); extra.classList.add('connection-other');
    const mainKeys = new Set(['type', 'provider_type', 'provider_id', 'embedding_api_base', 'rerank_api_base', 'embedding_api_key', 'rerank_api_key', 'embedding_model', 'rerank_model', 'embedding_dimensions', 'rerank_api_suffix', 'timeout']);
    for (const [key, value] of Object.entries(values)) {
      const wrapper = node('label', 'field'); wrapper.append(node('span', 'field-label', labels[key] || key));
      const row = node('div', 'copy-row');
      const formatted = value && typeof value === 'object' ? JSON.stringify(value) : value === null ? '' : String(value);
      const input = node('input'); input.readOnly = true; input.value = formatted; input.placeholder = '留空'; input.dataset.connection = name + '.' + key;
      const secret = key.endsWith('api_key'); input.type = secret ? 'password' : 'text'; input.setAttribute('aria-label', title + ' ' + (labels[key] || key));
      row.append(input);
      if (secret) {
        const show = node('button', 'copy-button', '显示'); show.type = 'button';
        show.addEventListener('click', () => { input.type = input.type === 'password' ? 'text' : 'password'; show.textContent = input.type === 'password' ? '显示' : '隐藏'; }); row.append(show);
      }
      const copy = node('button', 'copy-button', '复制'); copy.type = 'button'; copy.setAttribute('aria-label', '复制' + title + ' ' + (labels[key] || key)); copy.addEventListener('click', () => copyText(value, input)); row.append(copy);
      wrapper.append(row); (mainKeys.has(key) ? fields : extra).append(wrapper);
    }
    fields.append(extra); content.append(fields);
    const all = node('button', 'button secondary copy-all', '复制整组参数 JSON'); all.type = 'button'; all.addEventListener('click', () => copyText(JSON.stringify(values, null, 2))); content.append(all); container.append(content);
  }
}

async function refreshConnections() {
  if (!state || connectionLoading || connectionAction) return;
  connectionLoading = true; renderConnections();
  try {state.connections_management = await call('settings/connections');}
  catch (error) {notice(error.message || '无法读取项目提供商状态', true);}
  finally {connectionLoading = false; renderConnections();}
}

async function manageConnection(action, kind) {
  if (connectionAction || saving) return;
  connectionAction = action + ':' + kind; renderConnections(); status();
  try {
    const result = await call('settings/connections/' + action, {kind});
    state.connections_management = result;
    if (result.framework_providers) {state.providers = result.framework_providers; renderBackends(); renderMethodGuides();}
    notice(result.message || (action === 'sync' ? '已同步到项目。' : '已从项目删除。'));
  } catch (error) {
    notice(error.message || '项目提供商操作失败', true);
    try {state.connections_management = await call('settings/connections');} catch (_) { /* Keep the original operation error visible. */ }
  }
  finally {connectionAction = ''; renderConnections(); status();}
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
    const input = node('textarea'); input.id = 'embedding-texts'; input.rows = 3; input.value = testDrafts.embedding.texts;
    input.addEventListener('input', () => {testDrafts.embedding.texts = input.value;}); label.append(input); area.append(label);
  } else {
    const q = node('label', 'field'); q.append(node('span', 'field-label', '查询问题')); const input = node('input'); input.id = 'rerank-query'; input.value = testDrafts.rerank.query;
    input.addEventListener('input', () => {testDrafts.rerank.query = input.value;}); q.append(input);
    const d = node('label', 'field'); d.append(node('span', 'field-label', '候选文本（每行一条，最多16条）')); const docs = node('textarea'); docs.id = 'rerank-documents'; docs.rows = 3; docs.value = testDrafts.rerank.documents;
    docs.addEventListener('input', () => {testDrafts.rerank.documents = docs.value;}); d.append(docs); area.append(q, d);
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
  if (testing || saving || !validateConfiguration(testKind)) return;
  testing = true; $('run-test').disabled = true; $('test-embedding-tab').disabled = true; $('test-rerank-tab').disabled = true;
  const area = $('test-result'); area.classList.remove('error'); area.replaceChildren(node('div', 'empty-result', '正在执行真实模型请求，请稍候…'));
  const payload = {kind: testKind, settings: clone(draft)};
  if (testKind === 'embedding') payload.texts = $('embedding-texts').value.split(/\r?\n/).filter(text => text.trim());
  else {payload.query = $('rerank-query').value; payload.documents = $('rerank-documents').value.split(/\r?\n/).filter(text => text.trim());}
  try { renderResult(await call('settings/test', payload)); }
  catch (error) {
    area.classList.add('error');
    const dll = /onnxruntime_pybind11_state|DLL load failed/i.test(error.message || '');
    area.replaceChildren(node('strong', '', dll ? '本地运行环境尚未就绪' : '测试未通过'), node('p', '', dll ? 'ONNX Runtime 动态库无法加载，请先处理 Debug 页中的环境问题。' : error.message || '模型调用失败，请检查提供商与模型目录。'));
    if (dll) {const help = node('button', 'text-button', '查看 Debug 诊断 →'); help.type = 'button'; help.addEventListener('click', () => navigate('debug')); area.append(help);}
  }
  finally {await previewPaths(); testing = false; $('run-test').disabled = false; $('test-embedding-tab').disabled = false; $('test-rerank-tab').disabled = false;}
}

function render(snapshot) {
  state = snapshot; draft = clone(snapshot.settings); original = clone(draft); dirty = false;
  $('workspace').hidden = false; renderPresets(); renderBackends(); renderPathsEditor(); renderPaths(snapshot.paths); renderRuntime(); renderService(); renderConnections(); renderTestInputs(); status();
  $('plugin-version').textContent = snapshot.version || '0.5.1'; hydrateIcons(); navigate(currentRoute, {replace: true, scroll: false});
  if (state.requires_restart) notice('设置已保存；端口变更需要重启插件，对接参数页仍展示当前运行地址。');
}

async function save() {
  if (saving || testing || !validateConfiguration()) return;
  saving = true; status(); renderConnections();
  document.querySelectorAll('#configuration input, #configuration select, #presets button').forEach(el => el.disabled = true);
  try {const result = await call('settings/save', {revision: state.revision, settings: clone(draft)}); render(result); notice(result.message);}
  catch (error) {notice(error.message || '保存失败', true);}
  finally {
    saving = false;
    document.querySelectorAll('#configuration input, #configuration select, #presets button').forEach(el => {
      el.disabled = !!el.dataset.localKind && !(state.local_models?.[el.dataset.localKind] || []).length;
    });
    status(); renderConnections();
  }
}

$('save').addEventListener('click', save);
$('reload').addEventListener('click', async () => {try {render(await call('settings')); notice('已重新读取保存的配置。');} catch (error) {notice(error.message, true);}});
$('run-test').addEventListener('click', runTest);
$('refresh-models').addEventListener('click', previewPaths);
$('refresh-diagnostics').addEventListener('click', previewPaths);
$('install-runtime').addEventListener('click', installRuntime);
$('test-embedding-tab').addEventListener('click', () => {testKind = 'embedding'; renderTestInputs();});
$('test-rerank-tab').addEventListener('click', () => {testKind = 'rerank'; renderTestInputs();});
$('configuration').addEventListener('submit', event => event.preventDefault());
document.addEventListener('click', event => {const target = event.target.closest('[data-route]'); if (target) navigate(target.dataset.route);});
$('connections-menu').addEventListener('click', () => navigate(currentRoute.startsWith('connections/') ? currentRoute : 'connections/embedding'));
window.addEventListener('hashchange', () => navigate(location.hash.slice(1), {replace: true}));
hydrateIcons();

try {
  if (!bridge) throw new Error('请从AstrBot插件详情的「重排和嵌入」视图打开本页。');
  const context = await bridge.ready();
  if (context?.isDark || context?.theme === 'dark') document.documentElement.dataset.theme = 'dark';
  currentRoute = pages[location.hash.slice(1)] ? location.hash.slice(1) : 'backends';
  render(await call('settings'));
  if (context?.displayName?.includes('本地验证')) notice('本地验证环境：本地模型执行真实推理，聊天提供商使用测试替身。');
} catch (error) {
  $('fatal').hidden = false; $('fatal').textContent = error.message || '无法读取插件状态'; $('save-status').textContent = '连接失败';
}
