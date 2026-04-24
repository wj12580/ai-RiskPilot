/**
 * RiskPilot - 前端主应用
 */

// API 基础 URL
const API_BASE = '/api';

// 当前状态
let currentState = {
  page: 'analysis',
  uploadedFile: null,
  analysisResult: null,
  records: [],
  reviews: [],
  reviewColumns: [],     // 复盘文件列名列表
  reviewRecordId: null,   // 当前复盘的 record id
};

// 页面初始化
document.addEventListener('DOMContentLoaded', () => {
  initNavigation();
  initAnalysisPage();
  initRecordsPage();
  initReviewsPage();
  initKnowledgePage();

  // 默认显示分析页面
  showPage('analysis');

  // 加载全局统计（页面加载时也调用一次）
  loadGlobalStats();
});

// ── 全局统计加载与更新 ─────────────────────────────────────────────────────────
async function loadGlobalStats() {
  try {
    const res = await fetch(`${API_BASE}/records/stats`);
    if (!res.ok) return;
    const data = await res.json();

    // 更新统计卡片
    const el1 = document.getElementById('std-total-analysis');
    const el2 = document.getElementById('std-strategy-adjust');
    const el3 = document.getElementById('std-review-done');
    const el4 = document.getElementById('std-review-pending');

    if (el1) el1.textContent = data.analysis_count || 0;
    if (el2) el2.textContent = data.record_count || 0;
    if (el3) el3.textContent = data.review_done || 0;
    if (el4) el4.textContent = data.review_pending || 0;
  } catch (e) {
    console.warn('[统计] 加载失败', e);
  }
}

// ── 分析模块切换 ───────────────────────────────────────────────────────────
function onBizModuleChange() {
  const bizModule = document.getElementById('biz-module')?.value || '';
  const ruleHint = document.getElementById('rule-analysis-hint');
  
  if (ruleHint) {
    if (bizModule === 'rule') {
      ruleHint.classList.remove('hidden');
    } else {
      ruleHint.classList.add('hidden');
    }
  }

  applyDefaultAnalysisTagsByModule(bizModule);
}

function initNavigation() {
  const navItems = document.querySelectorAll('.nav-item');
  navItems.forEach(item => {
    item.addEventListener('click', () => {
      const page = item.dataset.page;
      showPage(page);
    });
  });
}

function showPage(page) {
  currentState.page = page;
  
  // 更新导航状态
  document.querySelectorAll('.nav-item').forEach(item => {
    item.classList.toggle('active', item.dataset.page === page);
  });
  
  // 显示对应页面 —— 知识库页面用 style.display 控制，其他用 class
  document.querySelectorAll('.page-section').forEach(section => {
    if (section.id === 'page-knowledge') {
      section.style.display = page === 'knowledge' ? 'block' : 'none';
    } else {
      section.classList.toggle('active', section.id === `page-${page}`);
    }
  });
  
  // 加载页面数据
  if (page === 'records') {
    loadRecords();
  } else if (page === 'reviews') {
    loadReviews();
  } else if (page === 'knowledge') {
    loadKnowledgeTopics();
  }

  // 每次切到分析页刷新统计（已移除stats-row，无需加载）
  // if (page === 'analysis') {
  //   loadGlobalStats();
  // }
}

// ── 分析页面 ─────────────────────────────────────────────────────────────────
function initAnalysisPage() {
  const uploadArea = document.getElementById('upload-area');
  const fileInput = document.getElementById('file-input');
  
  uploadArea.addEventListener('click', () => fileInput.click());
  
  fileInput.addEventListener('change', (e) => {
    if (e.target.files.length > 0) {
      handleFileUpload(e.target.files[0]);
    }
  });
  
  uploadArea.addEventListener('dragover', (e) => {
    e.preventDefault();
    uploadArea.classList.add('dragover');
  });
  
  uploadArea.addEventListener('dragleave', () => {
    uploadArea.classList.remove('dragover');
  });
  
  uploadArea.addEventListener('drop', (e) => {
    e.preventDefault();
    uploadArea.classList.remove('dragover');
    if (e.dataTransfer.files.length > 0) {
      handleFileUpload(e.dataTransfer.files[0]);
    }
  });
  
  document.getElementById('btn-run-analysis').addEventListener('click', runAnalysis);
  document.getElementById('btn-export-report').addEventListener('click', exportReport);
  document.getElementById('btn-save-record').addEventListener('click', saveToRecord);

  applyDefaultAnalysisTagsByModule(document.getElementById('biz-module')?.value || '');
}

async function handleFileUpload(file) {
  if (!file.name.match(/\.(csv|xlsx|xls)$/i)) {
    showToast('请上传 CSV 或 Excel 文件', 'error');
    return;
  }
  
  showLoading('正在上传文件...');
  
  const formData = new FormData();
  formData.append('file', file);
  
  try {
    const res = await fetch(`${API_BASE}/analysis/upload`, {
      method: 'POST',
      body: formData
    });
    
    const data = await res.json();
    hideLoading();
    
    if (!res.ok) {
      showToast(data.error || '上传失败', 'error');
      return;
    }
    
    currentState.uploadedFile = data;
    showFileInfo(data);
    populateColumnSelects(data.columns);
    
    // 填充渠道字段选择下拉框
    populateChannelColSelect(data.columns);
    
    // 重置渠道多选
    document.getElementById('channel-col-select').value = '';
    document.getElementById('channel-multiselect').innerHTML = '<div style="color:#9ca3af;font-size:0.85rem;padding:8px 0;">请先选择渠道字段</div>';
    
    showToast('文件上传成功', 'success');
  } catch (err) {
    hideLoading();
    showToast('上传失败: ' + err.message, 'error');
  }
}

function showFileInfo(info) {
  document.getElementById('file-info').classList.remove('hidden');
  document.getElementById('upload-area').classList.add('hidden');
  
  document.getElementById('info-filename').textContent = info.file_name;
  document.getElementById('info-rows').textContent = info.n_rows.toLocaleString();
  document.getElementById('info-cols').textContent = info.n_cols;
}

function populateColumnSelects(columns) {
  const targetSelect = document.getElementById('target-col');

  targetSelect.innerHTML = '<option value="">请选择</option>';

  columns.forEach(col => {
    const opt1 = new Option(col, col);
    targetSelect.add(opt1);
  });

  const exactLabel = (columns || []).find(col => String(col).toLowerCase() === 'label');
  targetSelect.value = exactLabel || '';

  renderFeatureCols(columns);
}

function populateChannelColSelect(columns) {
  const channelColSelect = document.getElementById('channel-col-select');

  channelColSelect.innerHTML = '<option value="">选择渠道字段</option>';

  columns.forEach(col => {
    const opt = new Option(col, col);
    channelColSelect.add(opt);
  });

  const defaultChannelCol =
    (columns || []).find(col => col === '渠道') ||
    (columns || []).find(col => col === '渠道组') ||
    (columns || []).find(col => String(col).toLowerCase() === 'channel');

  if (defaultChannelCol) {
    channelColSelect.value = defaultChannelCol;
    onChannelColChange();
  }
}

async function onChannelColChange() {
  const channelColSelect = document.getElementById('channel-col-select');
  const channelCol = channelColSelect.value;
  
  if (!channelCol) {
    document.getElementById('channel-multiselect').innerHTML = '<div style="color:#9ca3af;font-size:0.85rem;padding:8px 0;">请先选择渠道字段</div>';
    return;
  }
  
  if (!currentState.uploadedFile) {
    showToast('请先上传数据文件', 'error');
    return;
  }
  
  // 清空渠道多选
  document.getElementById('channel-multiselect').innerHTML = '<div style="color:#9ca3af;font-size:0.85rem;padding:8px 0;">正在加载渠道值...</div>';
  
  try {
    const res = await fetch(`${API_BASE}/analysis/column-values`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        file_id: currentState.uploadedFile.file_id,
        column_name: channelCol
      })
    });
    
    const data = await res.json();
    
    if (!res.ok) {
      document.getElementById('channel-multiselect').innerHTML = '<div style="color:#ef4444;font-size:0.85rem;padding:8px 0;">加载失败</div>';
      return;
    }
    
    // 渲染渠道多选
    renderChannelValues(data.unique_values);
    
  } catch (err) {
    document.getElementById('channel-multiselect').innerHTML = '<div style="color:#ef4444;font-size:0.85rem;padding:8px 0;">加载失败: ' + err.message + '</div>';
  }
}

function renderChannelValues(values) {
  const container = document.getElementById('channel-multiselect');
  container.innerHTML = '';
  
  if (!values || values.length === 0) {
    container.innerHTML = '<div style="color:#9ca3af;font-size:0.85rem;padding:8px 0;">该字段无有效值</div>';
    return;
  }
  
  // 全选行
  const allRow = document.createElement('div');
  allRow.style.cssText = 'display:flex;align-items:center;gap:6px;padding:4px 0;border-bottom:1px solid #e5e7eb;margin-bottom:4px;';
  allRow.innerHTML = `
    <input type="checkbox" id="channel-select-all" onchange="toggleAllChannelValues(this.checked)">
    <label for="channel-select-all" style="cursor:pointer;font-size:0.85rem;color:#374151;">全选</label>
    <span style="font-size:0.75rem;color:#6b7280;margin-left:4px;">(共${values.length}个)</span>
  `;
  container.appendChild(allRow);
  document.getElementById('channel-select-all').addEventListener('change', (e) => {
    container.querySelectorAll('.channel-value-cb').forEach(cb => cb.checked = e.target.checked);
    updateChannelCount();
  });
  
  // 逐个渲染（网格布局）
  const grid = document.createElement('div');
  grid.style.cssText = 'display:grid;grid-template-columns:repeat(auto-fill,minmax(80px,1fr));gap:4px;padding:4px 0;';
  
  values.forEach((val, idx) => {
    const item = document.createElement('div');
    item.style.cssText = 'display:flex;align-items:center;gap:2px;';
    item.innerHTML = `
      <input type="checkbox" class="channel-value-cb" id="ch_val_${idx}" value="${val}">
      <label for="ch_val_${idx}" title="${val}" style="cursor:pointer;font-size:0.78rem;color:#4b5563;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${val}</label>
    `;
    grid.appendChild(item);
    item.querySelector('.channel-value-cb').addEventListener('change', updateChannelCount);
  });
  
  container.appendChild(grid);
  
  // 默认全选
  document.getElementById('channel-select-all').checked = true;
  container.querySelectorAll('.channel-value-cb').forEach(cb => cb.checked = true);
  updateChannelCount();
}

function toggleAllChannelValues(checked) {
  document.querySelectorAll('.channel-value-cb').forEach(cb => {
    cb.checked = checked;
  });
  updateChannelCount();
}

function getSelectedChannelValues() {
  return Array.from(document.querySelectorAll('.channel-value-cb:checked')).map(cb => cb.value);
}

function updateChannelCount() {
  const total  = document.querySelectorAll('.channel-value-cb').length;
  const checked = document.querySelectorAll('.channel-value-cb:checked').length;
  const countEl = document.getElementById('channel-selected-count');
  if (!countEl) return;
  if (total === 0) {
    countEl.textContent = '';
  } else if (checked === 0) {
    countEl.textContent = '未选择（将分析全部渠道）';
    countEl.style.color = '#f59e0b';
  } else if (checked === total) {
    countEl.textContent = `已选全部 ${total} 个渠道`;
    countEl.style.color = '#10b981';
  } else {
    countEl.textContent = `已选 ${checked} / ${total} 个渠道`;
    countEl.style.color = '#6b7280';
  }
  // 同步全选框状态
  const allCb = document.getElementById('channel-select-all');
  if (allCb) {
    allCb.checked = total > 0 && checked === total;
    allCb.indeterminate = checked > 0 && checked < total;
  }
}

function renderFeatureCols(columns) {
  const container = document.getElementById('feature-cols-container');
  if (!container) return;

  if (!columns || columns.length === 0) {
    container.innerHTML = '<div style="color:#9ca3af;font-size:0.9rem;padding:10px 0;">暂无可用列</div>';
    updateFeatureSelectedCount();
    return;
  }

  const targetCol = document.getElementById('target-col').value;
  const excludePatterns = /^(id|user_id|loan_id|apply_id|created_at|updated_at)$/i;
  const excludedFields = new Set([
    'product', '渠道', '渠道组', 'order_id', 'customer_id',
    '时间', 'label', 'apply_time', 'apply_date'
  ].map(v => String(v).toLowerCase()));
  const filteredColumns = (columns || []).filter(col => {
    const normalized = String(col).toLowerCase();
    if (normalized === String(targetCol || '').toLowerCase()) return false;
    if (excludedFields.has(normalized)) return false;
    if (excludePatterns.test(col)) return false;
    return true;
  });

  container.innerHTML = '';

  const allRow = document.createElement('div');
  allRow.className = 'feature-select-all-row';
  allRow.innerHTML = `
    <input type="checkbox" id="feature-select-all" onchange="toggleAllFeatureCols(this.checked)">
    <label for="feature-select-all">全部选择</label>
    <span style="font-size:0.8rem;color:#6b7280;margin-left:8px;">共${filteredColumns.length} 列</span>
  `;
  container.appendChild(allRow);

  const grid = document.createElement('div');
  grid.className = 'feature-cols-grid';

  filteredColumns.forEach((col, idx) => {
    const item = document.createElement('div');
    item.className = 'feature-col-item';
    const cbId = `feat_col_${idx}`;
    item.innerHTML = `
      <input type="checkbox" id="${cbId}" class="feature-col-cb" value="${col}" onchange="updateFeatureSelectedCount()">
      <label for="${cbId}" title="${col}">${col}</label>
    `;
    grid.appendChild(item);
  });

  container.appendChild(grid);
  updateFeatureSelectedCount();
}

function toggleAllFeatureCols(checked) {
  document.querySelectorAll('.feature-col-cb').forEach(cb => {
    cb.checked = checked;
  });
  updateFeatureSelectedCount();
}

function getSelectedFeatureCols() {
  return Array.from(document.querySelectorAll('.feature-col-cb:checked')).map(cb => cb.value);
}

function updateFeatureSelectedCount() {
  const total = document.querySelectorAll('.feature-col-cb').length;
  const selected = document.querySelectorAll('.feature-col-cb:checked').length;
  const countEl = document.getElementById('feature-selected-count');
  if (countEl) {
    if (total === 0) {
      countEl.textContent = '';
    } else if (selected === 0) {
      countEl.textContent = '未选择任何特征列（选填，不选则分析全部）';
      countEl.style.color = '#f59e0b';
    } else if (selected === total) {
      countEl.textContent = `已选全部 ${total} 列`;
      countEl.style.color = '#10b981';
    } else {
      countEl.textContent = `已选 ${selected} / ${total} 列`;
      countEl.style.color = '#6b7280';
    }
  }
  // 同步全选框状态
  const allCb = document.getElementById('feature-select-all');
  if (allCb) {
    allCb.checked = total > 0 && selected === total;
    allCb.indeterminate = selected > 0 && selected < total;
  }
}

function getAnalysisTags() {
  const checkedPreset = Array.from(document.querySelectorAll('input[name="analysis_tag"]:checked')).map(cb => cb.value);
  const customChecked = Array.from(document.querySelectorAll('.custom-tag-cb:checked')).map(cb => cb.value);
  return [...checkedPreset, ...customChecked];
}

function applyDefaultAnalysisTagsByModule(bizModule) {
  const defaultMap = {
    rule: ['feature_iv', 'overdue_lift', 'strategy_layer'],
    model: ['model_score_eval', 'feature_iv', 'overdue_lift', 'psi_stability'],
  };
  const targetTags = defaultMap[bizModule];
  if (!targetTags) return;

  document.querySelectorAll('input[name="analysis_tag"]').forEach(cb => {
    cb.checked = targetTags.includes(cb.value);
  });
}

document.getElementById('btn-change-file').addEventListener('click', () => {
  currentState.uploadedFile = null;
  document.getElementById('file-info').classList.add('hidden');
  document.getElementById('upload-area').classList.remove('hidden');
  document.getElementById('file-input').value = '';
});

async function runAnalysis() {
  if (!currentState.uploadedFile) {
    showToast('请先上传数据文件', 'error');
    return;
  }
  
  const targetCol = document.getElementById('target-col').value;
  const nBins = document.getElementById('n-bins').value;
  const note = document.getElementById('analysis-note').value;
  const runButton = document.getElementById('btn-run-analysis');
  const originalRunButtonHtml = runButton ? runButton.innerHTML : '';

  const analysisTags = getAnalysisTags();
  const featureCols = getSelectedFeatureCols();

  const allColumns = currentState.uploadedFile.columns || [];
  const scorePatterns = /score|分数|prob|预测|model|risk/i;
  const guessedScoreCol = allColumns.find(c => scorePatterns.test(c)) || '';
  
  if (!targetCol) {
    showToast('请选择目标列（逾期标签）', 'error');
    return;
  }

  if (runButton) {
    runButton.disabled = true;
    runButton.innerHTML = '正在分析，请稍等';
  }
  
  showLoading('正在分析，请稍等');
  try {
    const configRes = await fetch(`${API_BASE}/analysis/llm-config`);
    const config = await configRes.json();
    showLoading(config.configured ? '正在分析，请稍等' : '正在分析，请稍等');
  } catch (e) {
    showLoading('正在分析，请稍等');
  }

  const bizModule = document.getElementById('biz-module')?.value || '';
  const isMultiModel = Array.isArray(featureCols) && featureCols.length >= 2;
  const isRuleAnalysis = bizModule === 'rule';
  
  let apiEndpoint;
  if (isRuleAnalysis) {
    apiEndpoint = `${API_BASE}/analysis/rule-analysis`;
  } else if (bizModule === 'model') {
    if (isMultiModel) {
      apiEndpoint = `${API_BASE}/analysis/model-correlation`;
    } else {
      apiEndpoint = `${API_BASE}/analysis/model-binning`;
    }
  } else {
    apiEndpoint = isMultiModel
      ? `${API_BASE}/analysis/multi-model`
      : `${API_BASE}/analysis/run`;
  }

  try {
    const channelCol = document.getElementById('channel-col-select')?.value || '';
    const channelValues = channelCol ? getSelectedChannelValues() : [];
    const bizScenario = document.getElementById('biz-scenario')?.value || '';
    const bizCountry = document.getElementById('biz-country')?.value || '';
    
    const payload = {
      file_id: currentState.uploadedFile.file_id,
      file_name: currentState.uploadedFile.file_name,
      file_type: currentState.uploadedFile.file_type,
      analysis_type: analysisTags.length > 0 ? analysisTags.join(',') : 'model_eval',
      analysis_tags: analysisTags,
      target_col: targetCol,
      score_col: guessedScoreCol || (featureCols[0] || ''),
      feature_cols: featureCols,
      n_bins: parseInt(nBins),
      user_note: note,
      use_agent: false,
      biz_scenario: bizScenario,
      biz_country: bizCountry,
      biz_module: bizModule,
      channel_col: channelCol,
      channel_values: channelValues,
    };
    
    if (isRuleAnalysis) {
      payload.rule_cols = featureCols;
    }

    const res = await fetch(apiEndpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    const data = await res.json();
    hideLoading();

    if (!res.ok) {
      showToast(data.error || '分析失败', 'error');
      return;
    }

    currentState.analysisResult = data;
    currentState.analysisResult._analysisTags = analysisTags;

    if (data.mode === 'rule_analysis') {
      displayRuleReportResults(data);
    } else if (data.mode === 'model_binning' || data.mode === 'model_correlation') {
      displayModelReportResults(data);
    } else if (data.mode === 'expert_analysis') {
      displayExpertAnalysisResults(data);
    } else if (data.mode === 'multi_model') {
      displayMultiModelResults(data);
    } else {
      displayResults(data);
    }

    showToast('分析完成', 'success');
    loadGlobalStats();
  } catch (err) {
    hideLoading();
    showToast('分析失败: ' + err.message, 'error');
  } finally {
    if (runButton) {
      runButton.disabled = false;
      runButton.innerHTML = originalRunButtonHtml;
    }
  }
}

function displayModelReportResults(data) {
  // 显示结果区域
  document.getElementById('analysis-result').classList.remove('hidden');

  // 隐藏 legacy 容器（原有指标卡片、分箱、相关性视图）
  document.getElementById('result-legacy-container').classList.add('hidden');
  // 隐藏旧的各卡片
  document.getElementById('result-bins-card').classList.add('hidden');
  document.getElementById('result-multi-model-card').classList.add('hidden');
  document.getElementById('result-model-binning-card').classList.add('hidden');
  document.getElementById('result-model-correlation-card').classList.add('hidden');

  // 显示新的模型报告卡片
  document.getElementById('result-model-report-card').classList.remove('hidden');

  // 填充元数据
  const metaEl = document.getElementById('model-report-meta');
  if (metaEl) {
    const summary = data.data_summary || {};
    const perf = data.performance || [];
    const sampleInfo = summary.total_samples
      ? `样本量：${summary.total_samples.toLocaleString()} | 逾期率：${((summary.overall_bad_rate || 0) * 100).toFixed(1)}% | 模型数量：${perf.length} 个`
      : `模型数量：${perf.length} 个`;
    metaEl.textContent = sampleInfo;
  }

  // 嵌入报告HTML（后端返回字段名为 html_report）
  const frame = document.getElementById('model-report-frame');
  const reportHtml = data.html_report || data.report_html || '';
  if (frame && reportHtml) {
    frame.srcdoc = reportHtml;
    // 自动调整 iframe 高度
    frame.onload = function() {
      try {
        const contentHeight = frame.contentDocument.documentElement.scrollHeight;
        frame.style.height = Math.max(contentHeight + 40, 800) + 'px';
      } catch(e) {
        // 跨域限制，设置默认高度
        frame.style.height = '1200px';
      }
    };
  } else if (frame && data.report_url) {
    frame.src = data.report_url;
  }

  // 保存数据供下载使用
  currentState.lastTaskId = data.task_id;
  currentState.reportFilename = data.report_filename || '';
  currentState.reportHtml = reportHtml;

  // AI 策略建议：保留在报告下方（已在 legacy container 外部，不会被隐藏）
  const suggestionsCard = document.getElementById('suggestions-card');
  if (suggestionsCard) {
    suggestionsCard.classList.remove('hidden');
  }

  // 渲染 AI 策略建议（优先使用 LLM 动态建议，无则降级到规则引擎）
  let suggestions = data.ai_suggestion;
  let suggestionSource = data.ai_suggestion_source || '';
  if (!suggestions || suggestions.length === 0) {
    suggestions = data.mode === 'model_correlation' ? generateCorrelationSuggestions(data) : generateBinningSuggestions(data);
    suggestionSource = '';
  }
  renderSuggestions(suggestions, suggestionSource);

  // 下载按钮事件
  const downloadBtn = document.getElementById('btn-download-report');
  if (downloadBtn) {
    downloadBtn.onclick = function() {
      downloadModelReport(data);
    };
  }
}

// ── 规则分析报告结果展示（iframe嵌入完整HTML报告）───────────────────────
function displayRuleReportResults(data) {
  // 显示结果区域
  document.getElementById('analysis-result').classList.remove('hidden');

  // 隐藏 legacy 容器（原有指标卡片、分箱、相关性视图）
  document.getElementById('result-legacy-container').classList.add('hidden');
  // 隐藏旧的各卡片
  document.getElementById('result-bins-card').classList.add('hidden');
  document.getElementById('result-multi-model-card').classList.add('hidden');
  document.getElementById('result-model-binning-card').classList.add('hidden');
  document.getElementById('result-model-correlation-card').classList.add('hidden');
  document.getElementById('result-model-report-card').classList.add('hidden');

  // 显示规则报告卡片
  document.getElementById('result-rule-report-card').classList.remove('hidden');

  // 填充元数据
  const metaEl = document.getElementById('rule-report-meta');
  if (metaEl) {
    const summary = data.data_summary || {};
    const ruleCount = (data.intercept_analysis || []).length;
    const sampleInfo = summary.total_samples
      ? `样本量：${summary.total_samples.toLocaleString()} | 逾期率：${((summary.overall_bad_rate || 0) * 100).toFixed(1)}% | 规则数量：${ruleCount} 个`
      : `规则数量：${ruleCount} 个`;
    metaEl.textContent = sampleInfo;
  }

  // 嵌入报告HTML（后端返回字段名为 html_report）
  const frame = document.getElementById('rule-report-frame');
  const reportHtml = data.html_report || data.report_html || '';
  if (frame && reportHtml) {
    frame.srcdoc = reportHtml;
    // 自动调整 iframe 高度
    frame.onload = function() {
      try {
        const contentHeight = frame.contentDocument.documentElement.scrollHeight;
        frame.style.height = Math.max(contentHeight + 40, 800) + 'px';
      } catch(e) {
        // 跨域限制，设置默认高度
        frame.style.height = '1200px';
      }
    };
  } else if (frame && data.report_url) {
    frame.src = data.report_url;
  }

  // 保存数据供下载使用
  currentState.lastTaskId = data.task_id;
  currentState.reportFilename = data.report_filename || '';
  currentState.reportHtml = reportHtml;

  // AI 策略建议
  const suggestionsCard = document.getElementById('suggestions-card');
  if (suggestionsCard) {
    suggestionsCard.classList.remove('hidden');
  }

  // 渲染 AI 策略建议
  let suggestions = data.ai_suggestion;
  let suggestionSource = data.ai_suggestion_source || '';
  if (!suggestions || suggestions.length === 0) {
    suggestions = generateRuleSuggestions(data);
    suggestionSource = '';
  }
  renderSuggestions(suggestions, suggestionSource);

  // 下载按钮事件
  const downloadBtn = document.getElementById('btn-download-rule-report');
  if (downloadBtn) {
    downloadBtn.onclick = function() {
      downloadRuleReport(data);
    };
  }
}

// 下载规则分析报告
function downloadRuleReport(data) {
  const html = data.report_html || currentState.reportHtml || '';
  if (!html) {
    showToast('报告内容为空，无法下载', 'error');
    return;
  }

  const blob = new Blob([html], { type: 'text/html;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = data.report_filename || `规则分析报告_${new Date().toISOString().slice(0,10)}.html`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// ── 规则分析兜底建议生成（规则引擎）────────────────────────────────────
function generateRuleSuggestions(data) {
  const suggestions = [];
  
  const intercept = data.intercept_analysis || [];
  const overdue = data.overdue_analysis || [];
  const serial = data.serial_strategy || [];
  const hitDist = data.hit_distribution || {};
  
  // 1. 规则拦截效果建议
  if (intercept.length > 0) {
    const highReject = intercept.filter(r => r.reject_rate > 0.3);
    const lowReject = intercept.filter(r => r.reject_rate < 0.05);
    
    if (highReject.length > 0) {
      suggestions.push({
        type: 'warning',
        title: '🚨 高拦截率规则警告',
        content: `以下规则拦截率过高（>30%）：${highReject.map(r => `${r.rule_name}(${(r.reject_rate*100).toFixed(1)}%)`).join('、')}。过高的拦截率可能导致优质客户流失。`,
        details: '建议调整拦截阈值或增加二次审核机制'
      });
    }
    
    if (lowReject.length > 0) {
      suggestions.push({
        type: 'info',
        title: '⚠️ 低拦截率规则关注',
        content: `以下规则拦截率过低（<5%）：${lowReject.map(r => `${r.rule_name}(${(r.reject_rate*100).toFixed(1)}%)`).join('、')}。可能存在规则失效或阈值设置不当的问题。`,
        details: '建议检查规则逻辑和阈值设置'
      });
    }
  }
  
  // 2. 规则与逾期关系建议
  if (overdue.length > 0) {
    const sorted = [...overdue].sort((a, b) => b.hit_bad_rate - a.hit_bad_rate);
    const topRisky = sorted.slice(0, 3);
    
    if (topRisky[0]) {
      const lift = topRisky[0].hit_lift || 0;
      const liftColor = lift > 2 ? 'danger' : lift > 1.5 ? 'warning' : 'info';
      suggestions.push({
        type: liftColor,
        title: `📊 最高风险规则：${topRisky[0].rule_name}`,
        content: `命中该规则的客户逾期率达 ${(topRisky[0].hit_bad_rate*100).toFixed(2)}%，是整体的 ${lift.toFixed(2)}x。拦截效果显著。`,
        details: '建议保持该规则的拦截策略，并持续监控'
      });
    }
    
    // 找出逾期率反而更低的规则（可能是反向指标）
    const lowRisk = sorted.filter(r => r.hit_lift < 0.8);
    if (lowRisk.length > 0) {
      suggestions.push({
        type: 'strategy',
        title: '🔄 反向指标规则',
        content: `以下规则命中后逾期率反而更低：${lowRisk.slice(0, 3).map(r => `${r.rule_name}(逾期率${(r.hit_bad_rate*100).toFixed(2)}%)`).join('、')}。可能是有效的"白名单"规则。`,
        details: '建议考虑将这些条件作为加分项或白名单'
      });
    }
  }
  
  // 3. 命中次数分布建议
  if (hitDist.avg_hits > 0) {
    if (hitDist.zero_hit_rate < 0.5) {
      suggestions.push({
        type: 'warning',
        title: '⚠️ 多规则交叉拦截过多',
        content: `平均每条数据命中 ${hitDist.avg_hits.toFixed(1)} 条规则，仅 ${(hitDist.zero_hit_rate*100).toFixed(1)}% 的客户零规则命中。过多的拦截可能导致拒客率过高。`,
        details: '建议优化规则组合，减少冗余规则'
      });
    }
  }
  
  // 4. 串行策略建议
  if (serial.length > 0) {
    // 找到平衡点（通过率适中且逾期可控）
    const optimal = serial.find(s => s.pass_rate > 0.3 && s.pass_rate < 0.7 && s.pass_bad_rate < 0.5);
    if (optimal) {
      suggestions.push({
        type: 'strategy',
        title: '🎯 推荐串行策略阈值',
        content: `建议采用 Q=${optimal.q}（阈值 ${optimal.threshold.toFixed(2)}），可获得 ${(optimal.pass_rate*100).toFixed(1)}% 的通过率和 ${(optimal.pass_bad_rate*100).toFixed(2)}% 的通过逾期率。`,
        details: `Lift: ${optimal.lift.toFixed(2)}x | 预计通过人数: ${optimal.pass_count.toLocaleString()}`
      });
    }
  }
  
  // 5. 规则优化建议
  if (intercept.length >= 3) {
    suggestions.push({
      type: 'strategy',
      title: '📋 规则组合优化建议',
      content: `当前共有 ${intercept.length} 条规则参与决策。建议定期进行规则有效性评估，移除低效规则，保留高效拦截规则。`,
      details: '可使用A/B测试验证规则效果'
    });
  }
  
  // 6. 欺诈检测建议
  suggestions.push({
    type: 'strategy',
    title: '🔍 欺诈风险规则建议',
    content: '对于高APR产品，欺诈风险是重要考量。建议增加：① 设备指纹识别规则；② 多头借贷检测规则；③ 欺诈名单实时查询。',
    details: '欺诈客户通常伪装良好，需多维度验证'
  });
  
  // 7. 差异化策略建议
  suggestions.push({
    type: 'strategy',
    title: '💰 差异化定价策略',
    content: '基于规则分析结果，建议对不同风险等级的客户采用差异化定价：高风险客户适用高利率覆盖风险，低风险优质客户可给予优惠提升忠诚度。',
    details: '结合分数和规则结果综合定价'
  });
  
  // 确保至少6条建议（只添加一条，不重复）
  if (suggestions.length < 6) {
    suggestions.push({
      type: 'info',
      title: '📈 持续监控建议',
      content: '建议持续监控各项规则的拦截效果，定期（建议每周/每月）复盘规则表现，及时调整失效规则。',
      details: '规则需要动态优化以适应市场变化'
    });
  }
  
  return suggestions.slice(0, 10);
}

// 下载模型分析报告
function downloadModelReport(data) {
  const html = data.report_html || currentState.reportHtml || '';
  if (!html) {
    showToast('报告内容为空，无法下载', 'error');
    return;
  }

  const blob = new Blob([html], { type: 'text/html;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = data.report_filename || `模型分析报告_${new Date().toISOString().slice(0,10)}.html`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
  showToast('报告下载成功', 'success');
}

// ── 专家深度分析结果展示 ──────────────────────────────────────────────
function displayExpertAnalysisResults(data) {
  // 显示结果区域
  document.getElementById('analysis-result').classList.remove('hidden');

  // 切换：隐藏其他视图
  document.getElementById('result-bins-card').classList.add('hidden');
  document.getElementById('result-multi-model-card').classList.add('hidden');
  document.getElementById('result-model-binning-card').classList.add('hidden');
  document.getElementById('result-model-correlation-card').classList.add('hidden');

  // 保存task_id用于下载
  currentState.lastTaskId = data.task_id;
  currentState.reportFilename = data.report_filename || '';

  // 获取专家报告数据
  const expertReports = data.expert_reports || {};

  // ── 渲染指标卡片 ─────────────────────────────────────────────────
  renderExpertMetrics(data, expertReports);

  // 专家分析模式也使用模型分箱视图来展示图表
  if (document.getElementById('result-model-binning-card')) {
    document.getElementById('result-model-binning-card').classList.remove('hidden');
    // 将专家报告数据转换为分箱分析格式进行展示
    try {
      renderExpertChartsAsBinning(expertReports, data);
    } catch (e) {
      console.warn('专家图表渲染失败:', e);
    }
  }

  // ── 渲染专家建议（简短版）────────────────────────────────────────
  renderExpertReport(data);

  // 添加下载按钮
  addDownloadButton(data);
}

// 将专家图表渲染为分箱分析格式
function renderExpertChartsAsBinning(expertReports, data) {
  const me = expertReports.model_engineer || {};
  const binResults = me.bin_results || [];
  
  if (binResults.length === 0) return;
  
  // 提取图表数据
  const charts = {
    model_ranking: null,
    badrate_comparison: null
  };
  
  // 渲染模型汇总
  const modelSummary = binResults.map((r, i) => ({
    model: r.model || `模型${i+1}`,
    auc: r.auc || 0,
    ks: r.ks || 0,
    bad_rate: r.bins && r.bins.length > 0 ? r.bins.reduce((s, b) => s + (b.bad_count || 0), 0) / r.bins.reduce((s, b) => s + (b.count || 0), 1) : 0,
    sample_count: r.bins ? r.bins.reduce((s, b) => s + (b.count || 0), 0) : 0
  }));
  
  // 更新KPI
  const container = document.getElementById('binning-summary-kpi');
  if (container) {
    container.innerHTML = `
      <div class="metric-card">
        <div class="metric-label">分析模型数</div>
        <div class="metric-value">${modelSummary.length}</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">样本总数</div>
        <div class="metric-value">${modelSummary.reduce((s, m) => s + (m.sample_count || 0), 0).toLocaleString()}</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">平均AUC</div>
        <div class="metric-value">${(modelSummary.reduce((s, m) => s + (m.auc || 0), 0) / (modelSummary.length || 1)).toFixed(4)}</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">平均KS</div>
        <div class="metric-value">${(modelSummary.reduce((s, m) => s + (m.ks || 0), 0) / (modelSummary.length || 1)).toFixed(4)}</div>
      </div>
    `;
  }
  
  // 更新模型排名表格
  const tableContainer = document.getElementById('binning-model-table');
  if (tableContainer) {
    const rows = modelSummary.map((m, i) => {
      const ksColor = (m.ks || 0) >= 0.35 ? 'color:#10b981;' : ((m.ks || 0) >= 0.25 ? 'color:#f59e0b;' : 'color:#ef4444;');
      const aucColor = (m.auc || 0) >= 0.75 ? 'color:#10b981;' : ((m.auc || 0) >= 0.65 ? 'color:#f59e0b;' : 'color:#ef4444;');
      return `
        <tr>
          <td>${i + 1}</td>
          <td title="${m.model || ''}">${(m.model || '未知').substring(0, 20)}</td>
          <td>${(m.sample_count || 0).toLocaleString()}</td>
          <td style="${aucColor}font-weight:bold;">${(m.auc || 0).toFixed(4)}</td>
          <td style="${ksColor}font-weight:bold;">${(m.ks || 0).toFixed(4)}</td>
          <td>${((m.bad_rate || 0) * 100).toFixed(2)}%</td>
        </tr>
      `;
    }).join('');
    
    tableContainer.innerHTML = `
      <table class="data-table">
        <thead>
          <tr>
            <th>#</th>
            <th>模型名称</th>
            <th>样本数</th>
            <th>AUC</th>
            <th>KS</th>
            <th>逾期率</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    `;
  }
  
  // 更新分箱详情
  const tabsContainer = document.getElementById('binning-model-tabs');
  if (tabsContainer) {
    tabsContainer.innerHTML = binResults.map((r, i) => `
      <button class="btn btn-sm ${i === 0 ? 'btn-primary' : 'btn-secondary'}" 
              onclick="showExpertBinningModelDetail(${i})" 
              id="binning-tab-${i}">
        ${(r.model || `模型${i+1}`).substring(0, 15)}
      </button>
    `).join('');
    
    if (binResults.length > 0) {
      showExpertBinningModelDetail(0, binResults);
    }
  }
}

// 显示专家分析的分箱详情
function showExpertBinningModelDetail(index, binResultsOverride) {
  const data = window.currentState?.expertReportsData;
  const binResults = binResultsOverride || (data?.model_engineer?.bin_results) || [];
  
  if (index < 0 || index >= binResults.length) return;
  
  const result = binResults[index];
  const bins = result.bins || [];
  
  const contentContainer = document.getElementById('binning-detail-content');
  if (!contentContainer) return;
  
  const modelInfo = `
    <div style="margin-bottom:16px;padding:12px;background:#f0f9ff;border-radius:8px;">
      <strong>模型：${result.model || '未知'}</strong>
      <span style="margin-left:16px;">AUC: ${(result.auc || 0).toFixed(4)}</span>
      <span style="margin-left:16px;">KS: ${(result.ks || 0).toFixed(4)}</span>
    </div>
  `;
  
  const tableRows = bins.map((bin, i) => {
    const badRate = (bin.bad_rate || 0) * 100;
    const rateColor = badRate > 10 ? '#ef4444' : (badRate > 5 ? '#f59e0b' : '#10b981');
    
    return `
      <tr>
        <td>${i + 1}</td>
        <td>${(bin.score_min || 0).toFixed(4)}</td>
        <td>${(bin.score_max || 0).toFixed(4)}</td>
        <td>${(bin.count || 0).toLocaleString()}</td>
        <td>${(bin.bad_count || 0).toLocaleString()}</td>
        <td style="color:${rateColor};font-weight:bold;">${badRate.toFixed(2)}%</td>
      </tr>
    `;
  }).join('');
  
  contentContainer.innerHTML = `
    ${modelInfo}
    <div class="table-container" style="max-height:400px;overflow-y:auto;">
      <table class="data-table">
        <thead>
          <tr>
            <th>箱号</th>
            <th>分数下限</th>
            <th>分数上限</th>
            <th>样本数</th>
            <th>坏样本数</th>
            <th>逾期率</th>
          </tr>
        </thead>
        <tbody>${tableRows}</tbody>
      </table>
    </div>
  `;
}

// ═══════════════════════════════════════════════════════════════════════════════
// ── 模型分箱分析结果展示 ─────────────────────────────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════════
function displayModelBinningResults(data) {
  // 显示结果区域
  document.getElementById('analysis-result').classList.remove('hidden');

  // 切换：隐藏其他视图，显示模型分箱视图
  document.getElementById('result-bins-card').classList.add('hidden');
  document.getElementById('result-multi-model-card').classList.add('hidden');
  document.getElementById('result-model-binning-card').classList.remove('hidden');
  document.getElementById('result-model-correlation-card').classList.add('hidden');

  // 保存task_id用于下载
  currentState.lastTaskId = data.task_id;
  currentState.reportFilename = data.report_filename || '';
  currentState.binningData = data; // 保存数据供标签页切换使用

  // 填充顶部指标卡片
  const summary = data.data_summary || {};
  document.getElementById('metric-total').textContent = (summary.total_samples || 0).toLocaleString();
  document.getElementById('metric-bad').textContent = (summary.total_bad || 0).toLocaleString();
  // 兼容字符串格式 ("15.23%") 和数值格式 (0.1523)
  const badRateVal = typeof summary.overall_bad_rate === 'string' 
    ? parseFloat(summary.overall_bad_rate.replace('%', '')) / 100
    : (summary.overall_bad_rate || 0);
  document.getElementById('metric-bad-rate').textContent = (badRateVal * 100).toFixed(2) + '%';
  
  // 取最优模型的KS/AUC
  const modelSummary = data.model_summary || [];
  const bestModel = modelSummary.length > 0 ? modelSummary[0] : {};
  const avgKs = modelSummary.length > 0 
    ? modelSummary.reduce((s, m) => s + (m.ks || 0), 0) / modelSummary.length 
    : 0;
  const avgAuc = modelSummary.length > 0 
    ? modelSummary.reduce((s, m) => s + (m.auc || 0), 0) / modelSummary.length 
    : 0;
  
  document.getElementById('metric-ks').textContent = avgKs.toFixed(4);
  document.getElementById('metric-auc').textContent = avgAuc.toFixed(4);
  document.getElementById('metric-psi').textContent = '-';

  setMetricColor('metric-ks', avgKs, 0.35, 0.25);
  setMetricColor('metric-auc', avgAuc, 0.75, 0.65);

  // 默认显示模型汇总标签
  switchBinningTab('summary');
  
  // 渲染模型汇总数据
  renderBinningSummary(data);
  
  // 渲染性能图表
  renderBinningCharts(data);
  
  // 渲染分箱详情
  renderBinningDetails(data);
  
  // 添加下载按钮
  addDownloadButton(data);
  
  // 渲染 AI 策略建议
  const binSuggestions = data.ai_suggestion;
  const binSource = data.ai_suggestion_source || '';
  if (binSuggestions && binSuggestions.length > 0) {
    renderSuggestions(binSuggestions, binSource);
  } else {
    renderSuggestions(generateBinningSuggestions(data));
  }
}

// 模型分箱Tab切换
function switchBinningTab(tab) {
  const tabs = ['summary', 'chart', 'detail'];
  tabs.forEach(t => {
    const panel = document.getElementById(`binning-panel-${t}`);
    const btn = document.getElementById(`btn-binning-${t}`);
    if (panel) {
      panel.classList.toggle('hidden', t !== tab);
    }
    if (btn) {
      btn.classList.toggle('btn-primary', t === tab);
      btn.classList.toggle('btn-secondary', t !== tab);
    }
  });
}

// 渲染模型汇总
function renderBinningSummary(data) {
  const container = document.getElementById('binning-summary-kpi');
  if (!container) return;
  
  const modelSummary = data.model_summary || [];
  const dataSummary = data.data_summary || {};
  
  // 兼容逾期率格式（字符串或数值）
  const badRateVal = typeof dataSummary.overall_bad_rate === 'string'
    ? parseFloat(dataSummary.overall_bad_rate.replace('%', '')) / 100
    : (dataSummary.overall_bad_rate || 0);
  
  // KPI卡片
  container.innerHTML = `
    <div class="metric-card">
      <div class="metric-label">分析模型数</div>
      <div class="metric-value">${modelSummary.length}</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">样本总数</div>
      <div class="metric-value">${(dataSummary.total_samples || 0).toLocaleString()}</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">整体逾期率</div>
      <div class="metric-value">${(badRateVal * 100).toFixed(2)}%</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">平均AUC</div>
      <div class="metric-value">${(modelSummary.reduce((s, m) => s + (m.auc || 0), 0) / (modelSummary.length || 1)).toFixed(4)}</div>
    </div>
  `;
  
  // 模型排名表格
  const tableContainer = document.getElementById('binning-model-table');
  if (!tableContainer || modelSummary.length === 0) {
    if (tableContainer) tableContainer.innerHTML = '<p style="color:#9ca3af;text-align:center;">暂无模型数据</p>';
    return;
  }
  
  const rows = modelSummary.map((m, i) => {
    const ksColor = (m.ks || 0) >= 0.35 ? 'color:#10b981;' : ((m.ks || 0) >= 0.25 ? 'color:#f59e0b;' : 'color:#ef4444;');
    const aucColor = (m.auc || 0) >= 0.75 ? 'color:#10b981;' : ((m.auc || 0) >= 0.65 ? 'color:#f59e0b;' : 'color:#ef4444;');
    return `
      <tr>
        <td>${i + 1}</td>
        <td title="${m.model || ''}">${(m.model || '未知').substring(0, 20)}</td>
        <td>${(m.sample_count || 0).toLocaleString()}</td>
        <td style="${aucColor}font-weight:bold;">${(m.auc || 0).toFixed(4)}</td>
        <td style="${ksColor}font-weight:bold;">${(m.ks || 0).toFixed(4)}</td>
        <td>${((m.bad_rate || 0) * 100).toFixed(2)}%</td>
        <td>${((m.iv || 0)).toFixed(4)}</td>
        <td>${((m.lift_top1 || 0)).toFixed(2)}</td>
      </tr>
    `;
  }).join('');
  
  tableContainer.innerHTML = `
    <table class="data-table">
      <thead>
        <tr>
          <th>#</th>
          <th>模型名称</th>
          <th>样本数</th>
          <th>AUC</th>
          <th>KS</th>
          <th>逾期率</th>
          <th>IV</th>
          <th>Top1 Lift</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

// 渲染性能图表
function renderBinningCharts(data) {
  const charts = data.charts || {};
  
  // 模型排名图表 (后端返回键名: 01_模型排名)
  const rankingImg = document.getElementById('img-binning-ranking');
  if (rankingImg && charts['01_模型排名']) {
    rankingImg.src = 'data:image/png;base64,' + charts['01_模型排名'];
  }
  
  // 逾期率对比图 (后端返回键名: 02_逾期率对比)
  const badrateImg = document.getElementById('img-binning-badrate');
  if (badrateImg && charts['02_逾期率对比']) {
    badrateImg.src = 'data:image/png;base64,' + charts['02_逾期率对比'];
  }
}

// 渲染分箱详情
function renderBinningDetails(data) {
  const allResults = data.all_results || [];
  
  // 模型选择标签
  const tabsContainer = document.getElementById('binning-model-tabs');
  if (!tabsContainer) return;
  
  tabsContainer.innerHTML = allResults.map((r, i) => `
    <button class="btn btn-sm ${i === 0 ? 'btn-primary' : 'btn-secondary'}" 
            onclick="showBinningModelDetail(${i})" 
            id="binning-tab-${i}">
      ${(r.model_name || `模型${i+1}`).substring(0, 15)}
    </button>
  `).join('');
  
  // 默认显示第一个模型
  if (allResults.length > 0) {
    currentState.currentBinningModelIndex = 0;
    showBinningModelDetail(0);
  }
}

// 显示指定模型的分箱详情
function showBinningModelDetail(index) {
  const data = currentState.binningData;
  if (!data || !data.all_results) return;
  
  const allResults = data.all_results;
  if (index < 0 || index >= allResults.length) return;
  
  currentState.currentBinningModelIndex = index;
  
  // 更新标签样式
  document.querySelectorAll('#binning-model-tabs .btn').forEach((btn, i) => {
    btn.classList.toggle('btn-primary', i === index);
    btn.classList.toggle('btn-secondary', i !== index);
  });
  
  const result = allResults[index];
  const bins = result.bins || [];
  
  // 渲染分箱详情表格
  const contentContainer = document.getElementById('binning-detail-content');
  if (!contentContainer) return;
  
  // 模型信息
  const modelInfo = `
    <div style="margin-bottom:16px;padding:12px;background:#f0f9ff;border-radius:8px;">
      <strong>模型：${result.model_name || '未知'}</strong>
      <span style="margin-left:16px;">AUC: ${(result.auc || 0).toFixed(4)}</span>
      <span style="margin-left:16px;">KS: ${(result.ks || 0).toFixed(4)}</span>
      <span style="margin-left:16px;">排序方式: ${result.sort_direction || '倒序'}</span>
    </div>
  `;
  
  // 分箱表格
  const tableRows = bins.map((bin, i) => {
    const badRate = (bin.bad_rate || 0) * 100;
    const lift = bin.lift || 1;
    const rateColor = badRate > 10 ? '#ef4444' : (badRate > 5 ? '#f59e0b' : '#10b981');
    
    return `
      <tr>
        <td>${bin.bin_no || (i + 1)}</td>
        <td>${(bin.score_min || 0).toFixed(4)}</td>
        <td>${(bin.score_max || 0).toFixed(4)}</td>
        <td>${(bin.count || 0).toLocaleString()}</td>
        <td>${(bin.bad_count || 0).toLocaleString()}</td>
        <td>${((bin.cum_bad_rate || 0) * 100).toFixed(2)}%</td>
        <td>${((bin.cum_good_rate || 0) * 100).toFixed(2)}%</td>
        <td style="color:${rateColor};font-weight:bold;">${badRate.toFixed(2)}%</td>
        <td>${lift.toFixed(2)}</td>
        <td>${(bin.cum_ks || 0).toFixed(4)}</td>
        <td>${(bin.max_ks || 0).toFixed(4)}</td>
      </tr>
    `;
  }).join('');
  
  contentContainer.innerHTML = `
    ${modelInfo}
    <div class="table-container" style="max-height:400px;overflow-y:auto;">
      <table class="data-table">
        <thead>
          <tr>
            <th>箱号</th>
            <th>分数下限</th>
            <th>分数上限</th>
            <th>样本数</th>
            <th>坏样本数</th>
            <th>Cum Bad%</th>
            <th>Cum Good%</th>
            <th>逾期率</th>
            <th>Lift</th>
            <th>Cum KS</th>
            <th>Max KS</th>
          </tr>
        </thead>
        <tbody>${tableRows}</tbody>
      </table>
    </div>
  `;
}

// ═══════════════════════════════════════════════════════════════════════════════
// ── AI 策略建议生成（分箱分析）- 动态数据驱动版 ─────────────────────────────────
// 【修改说明】不再使用固定模板框架，而是根据实际数据发现问题，再给出针对性建议
// 【重要】确保至少生成6条策略建议
function generateBinningSuggestions(data) {
  const suggestions = [];
  const modelSummary = data.model_summary || [];
  const dataSummary = data.data_summary || {};
  const allResults = data.all_results || [];
  
  if (modelSummary.length === 0) {
    return suggestions;
  }
  
  // ── Step 1: 先分析数据，发现问题 ─────────────────────────────────────────
  const avgAuc = modelSummary.reduce((s, m) => s + (m.auc || 0), 0) / modelSummary.length;
  const avgKs = modelSummary.reduce((s, m) => s + (m.ks || 0), 0) / modelSummary.length;
  
  // 兼容逾期率格式
  const badRateRaw = typeof dataSummary.overall_bad_rate === 'string'
    ? parseFloat(dataSummary.overall_bad_rate.replace('%', '')) / 100
    : (dataSummary.overall_bad_rate || 0);
  const overallBadRate = badRateRaw * 100;
  
  // 按 AUC 排序找最优模型
  const sortedByAuc = [...modelSummary].sort((a, b) => (b.auc || 0) - (a.auc || 0));
  const topModel = sortedByAuc[0];
  
  // 分析分箱质量
  let binningIssue = null;
  let binningQuality = null;
  if (allResults.length > 0) {
    const bestResult = allResults[0];
    const bins = bestResult.bins || [];
    if (bins.length > 0) {
      const badRates = bins.map(b => (b.bad_rate || 0) * 100);
      let isMonotonic = true;
      let nonMonotonicIndex = -1;
      for (let i = 1; i < badRates.length; i++) {
        if (badRates[i] < badRates[i-1]) {
          isMonotonic = false;
          nonMonotonicIndex = i;
          break;
        }
      }
      
      if (isMonotonic) {
        binningQuality = { ok: true, min: Math.min(...badRates), max: Math.max(...badRates) };
      } else {
        binningIssue = { index: nonMonotonicIndex, rates: badRates };
      }
    }
  }
  
  // ── Step 2: 根据实际问题生成针对性建议（确保至少6条） ─────────────────────────────────────
  
  // 【洞察1】模型性能评估
  if (avgAuc < 0.65 && avgKs < 0.25) {
    suggestions.push({
      type: 'danger',
      title: '🚨 模型性能较弱',
      content: `${modelSummary.length} 个模型平均 AUC=${avgAuc.toFixed(4)}、KS=${avgKs.toFixed(4)}，区分能力不足。建议优先优化模型质量。`,
      details: `建议：① 检查特征质量；② 考虑更换算法；③ 分析标签是否存在噪声`
    });
  } else if (avgAuc >= 0.70 && avgKs >= 0.30) {
    suggestions.push({
      type: 'success',
      title: '✅ 模型性能良好',
      content: `平均 AUC=${avgAuc.toFixed(4)}、KS=${avgKs.toFixed(4)}，满足风控需求，可以考虑部署策略。`,
      details: `最优模型：${topModel?.model_name || topModel?.model || '未知'}（AUC=${(topModel?.auc || 0).toFixed(4)}）`
    });
  } else {
    suggestions.push({
      type: 'warning',
      title: '⚠️ 模型性能有提升空间',
      content: `平均 AUC=${avgAuc.toFixed(4)}、KS=${avgKs.toFixed(4)}，基本可用但未达优秀。`,
      details: `建议持续优化特征工程或模型参数`
    });
  }
  
  // 【洞察2】分箱质量问题
  if (binningIssue) {
    suggestions.push({
      type: 'warning',
      title: '⚠️ 分箱存在逾期率倒挂',
      content: `第 ${binningIssue.index + 1} 箱逾期率低于前一箱，出现倒挂现象。这会导致 cutoff 附近判断混乱。`,
      details: `建议：① 检查该箱内客群特征；② 考虑合并相邻箱；③ 使用 WOE 编码替代原始分数`
    });
  } else if (binningQuality && binningQuality.ok) {
    const lift = binningQuality.max / (binningQuality.min || 0.01);
    suggestions.push({
      type: 'info',
      title: '📊 分箱单调性良好',
      content: `各箱逾期率呈单调递增趋势，逾期率范围 ${binningQuality.min.toFixed(2)}% ~ ${binningQuality.max.toFixed(2)}%，Lift=${lift.toFixed(2)}。`,
      details: `模型分数能有效区分客户风险等级，可以基于此制定 cutoff 策略`
    });
  }
  
  // 【洞察3】逾期率风险评估
  if (overallBadRate > 15) {
    suggestions.push({
      type: 'danger',
      title: '🚨 逾期率偏高，需收紧风控',
      content: `整体逾期率 ${overallBadRate.toFixed(2)}% 超过安全阈值。建议收紧风控策略以降低风险。`,
      details: `措施：① 提高 cutoff 阈值；② 分析高风险客群特征；③ 加强贷前审核；④ 增加反欺诈规则`
    });
  } else if (overallBadRate < 8) {
    suggestions.push({
      type: 'success',
      title: '✅ 逾期率控制良好',
      content: `整体逾期率 ${overallBadRate.toFixed(2)}% 处于健康水平。资产质量良好。`,
      details: `可以适当优化策略追求业务增长，但仍需保持风险警惕`
    });
  } else {
    suggestions.push({
      type: 'warning',
      title: '📈 逾期率处于中等水平',
      content: `整体逾期率 ${overallBadRate.toFixed(2)}% 处于可接受范围，建议持续监控并优化风控策略。`,
      details: `关注逾期率变化趋势，及时调整风控策略`
    });
  }
  
  // 【洞察4】多模型选择
  if (modelSummary.length >= 2) {
    const poorModels = modelSummary.filter(m => (m.auc || 0) < 0.65);
    if (poorModels.length > 0) {
      suggestions.push({
        type: 'strategy',
        title: '🎯 多模型选择建议',
        content: `${poorModels.length} 个模型 AUC < 0.65，表现较弱。建议仅使用头部模型，或对这些模型进行特征优化。`,
        details: `弱模型：${poorModels.slice(0, 3).map(m => m.model_name || m.model).join('、')}`
      });
    } else if (modelSummary.length > 3) {
      suggestions.push({
        type: 'strategy',
        title: '💡 多模型融合建议',
        content: `${modelSummary.length} 个模型均达到基础门槛（AUC>=0.65）。建议采用加权融合策略，使用 top-3 模型加权平均。`,
        details: `可提升风控稳定性，减少单模型波动风险`
      });
    }
  }
  
  // 【洞察5】最优模型 cutoff 建议
  if (topModel && binningQuality && binningQuality.ok) {
    const sortDirection = topModel.sort_direction || '倒序';
    const lowBadRate = binningQuality.min;
    const highBadRate = binningQuality.max;
    
    suggestions.push({
      type: 'strategy',
      title: '🎯 最优模型 cutoff 建议',
      content: `基于 ${topModel.model_name || topModel.model || '最优模型'} 的分箱结果，逾期率从 ${lowBadRate.toFixed(2)}% 变化到 ${highBadRate.toFixed(2)}%。`,
      details: `排序方式 ${sortDirection}，建议设定合理阈值区间，具体 cutoff 值需根据业务容忍度确定`
    });
  }
  
  // 【洞察6】欺诈检测建议（首贷场景）
  if (modelSummary.length >= 1) {
    const欺诈Score = (topModel?.auc || 0) < 0.60 ? '欺诈检测能力偏弱' : '欺诈检测能力尚可';
    suggestions.push({
      type: 'strategy',
      title: '🔍 欺诈检测策略建议',
      content: `${欺诈Score}，建议加强以下措施：① 设备指纹识别；② 多头借贷检测；③ 欺诈名单库实时查询。`,
      details: `对于首贷客户，尤其需要关注欺诈风险`
    });
  }
  
  // 【洞察7】特征稳定性建议
  if (allResults.length > 0 && allResults[0].bins) {
    const topModelName = topModel?.model_name || topModel?.model || '最优模型';
    suggestions.push({
      type: 'info',
      title: '📉 特征稳定性监控建议',
      content: `建议对 ${topModelName} 的核心特征进行 PSI 监控，及时发现特征漂移并重新训练模型。`,
      details: `特征稳定性是模型长期有效的关键`
    });
  }
  
  // 【洞察8】差异化定价建议
  if (binningQuality && binningQuality.ok && modelSummary.length >= 1) {
    const topModelName = topModel?.model_name || topModel?.model || '主要模型';
    suggestions.push({
      type: 'strategy',
      title: '💰 差异化风险定价建议',
      content: `基于 ${topModelName} 的分箱结果，建议对不同风险等级的客户采用差异化定价策略。`,
      details: `高风险客户适用高定价，低风险客户可给予优惠以提升忠诚度`
    });
  }
  
  return suggestions;
}

// ── 渲染AI策略建议 ───────────────────────────────────────────────────────────
function renderSuggestions(suggestions, source) {
  const container = document.getElementById('suggestions-list');
  if (!container) return;
  
  // 兼容：suggestions 可以是数组或对象
  let suggestionList = suggestions;
  if (!Array.isArray(suggestions)) {
    // 如果是对象（可能是 expert_reports 格式），提取其中的建议
    if (suggestions.suggestions && Array.isArray(suggestions.suggestions)) {
      suggestionList = suggestions.suggestions;
    } else {
      suggestionList = [];
    }
  }
  
  if (suggestionList.length === 0) {
    container.innerHTML = '<p style="color:#9ca3af;text-align:center;">暂无策略建议</p>';
    return;
  }
  
  const levelClass = (type) => {
    const map = {
      'performance': 'success',
      'sort': 'info',
      'quality': 'warning',
      'strategy': 'primary',
      'primary': 'primary',
      'business': 'warning',
      'info': 'info',
      'success': 'success',
      'warning': 'warning',
      'danger': 'danger',
    };
    return map[type] || 'info';
  };
  
  // 简单 Markdown 渲染：加粗 **text** → <b>text</b>，换行
  const renderMd = (text) => {
    if (!text) return '';
    return text
      .replace(/\*\*(.*?)\*\*/g, '<b>$1</b>')
      .replace(/\n/g, '<br>');
  };
  
  // 来源标签
  const sourceTag = source === 'llm'
    ? '<span style="display:inline-block;background:linear-gradient(135deg,#eff6ff,#dbeafe);color:#2563eb;font-size:11px;padding:2px 8px;border-radius:10px;margin-left:8px;vertical-align:middle;">🤖 AI 动态分析</span>'
    : '';
  
  const html = suggestionList.map(s => `
    <div class="suggestion-item ${levelClass(s.type)}">
      <div class="suggestion-title">${s.title || s.type || '建议'}${sourceTag}</div>
      <div class="suggestion-content">${renderMd(s.content || s.evaluation || s.diagnosis || '')}</div>
      ${s.details ? `<div class="suggestion-details">${renderMd(s.details)}</div>` : ''}
    </div>
  `).join('');
  
  container.innerHTML = html;
}

// ═══════════════════════════════════════════════════════════════════════════════
// ── 模型相关性分析结果展示 ─────────────────────────────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════════
function displayModelCorrelationResults(data) {
  // 显示结果区域
  document.getElementById('analysis-result').classList.remove('hidden');

  // 切换：隐藏其他视图，显示模型相关性视图
  document.getElementById('result-bins-card').classList.add('hidden');
  document.getElementById('result-multi-model-card').classList.add('hidden');
  document.getElementById('result-model-binning-card').classList.add('hidden');
  document.getElementById('result-model-correlation-card').classList.remove('hidden');

  // 保存task_id用于下载
  currentState.lastTaskId = data.task_id;
  currentState.reportFilename = data.report_filename || '';
  currentState.corrData = data; // 保存数据供标签页切换使用

  // 填充顶部指标卡片
  const perf = data.performance || [];
  const avgAuc = perf.length > 0 ? perf.reduce((s, p) => s + (p.auc || 0), 0) / perf.length : 0;
  const avgKs = perf.length > 0 ? perf.reduce((s, p) => s + (p.ks || 0), 0) / perf.length : 0;
  const avgBadRate = perf.length > 0 ? perf.reduce((s, p) => s + (p.bad_rate || 0), 0) / perf.length : 0;
  
  document.getElementById('metric-total').textContent = (data.data_summary?.total_samples || 0).toLocaleString();
  document.getElementById('metric-bad').textContent = (data.data_summary?.bad_samples || '-').toLocaleString();
  document.getElementById('metric-bad-rate').textContent = (avgBadRate * 100).toFixed(2) + '%';
  document.getElementById('metric-ks').textContent = avgKs.toFixed(4);
  document.getElementById('metric-auc').textContent = avgAuc.toFixed(4);

  setMetricColor('metric-ks', avgKs, 0.35, 0.25);
  setMetricColor('metric-auc', avgAuc, 0.75, 0.65);

  // 默认显示性能总览标签
  switchCorrTab('performance');
  
  // 渲染相关性分析数据
  renderCorrelationPerformance(data);
  renderCorrelationCharts(data);
  renderCorrelationComplement(data);
  renderCorrelationStrategy(data);
  renderCorrelationRoc(data);
  
  // 添加下载按钮
  addDownloadButton(data);
  
  // 渲染 AI 策略建议（优先 LLM 动态建议，降级到规则引擎）
  const corrSuggestions = data.ai_suggestion;
  const corrSource = data.ai_suggestion_source || '';
  if (corrSuggestions && corrSuggestions.length > 0) {
    renderSuggestions(corrSuggestions, corrSource);
  } else {
    renderSuggestions(generateCorrelationSuggestions(data));
  }
}

// ── AI策略建议生成（相关性分析）- 动态数据驱动版 ─────────────────────────────────
// 【修改说明】不再使用固定模板框架，而是根据实际数据发现问题，再给出针对性建议
// 【重要】确保至少生成6条策略建议
function generateCorrelationSuggestions(data) {
  const suggestions = [];
  const perf = data.performance || [];
  const corr = data.correlation || [];
  const comp = data.complementarity || [];
  const strategy = data.strategy_metrics || {};
  
  if (perf.length === 0) return suggestions;
  
  // ── Step 1: 先分析数据，发现问题 ─────────────────────────────────────────
  const avgAuc = perf.reduce((s, p) => s + (p.auc || 0), 0) / perf.length;
  const avgKs = perf.reduce((s, p) => s + (p.ks || 0), 0) / perf.length;
  const avgCov = perf.reduce((s, p) => s + (p.coverage || 0), 0) / perf.length;
  const avgBadRate = perf.reduce((s, p) => s + (p.bad_rate || 0), 0) / perf.length;
  
  // 按KS排序找最优模型
  const sortedByKs = [...perf].sort((a, b) => (b.ks || 0) - (a.ks || 0));
  const topModel = sortedByKs[0];
  const top3Models = sortedByKs.slice(0, 3);
  
  // 分析高相关模型对
  const highCorrPairs = [];
  corr.forEach(item => {
    const corrVal = Math.abs(item.correlation || item.corr || 0);
    if (corrVal > 0.85) {
      highCorrPairs.push({
        a: item.model_a || item.col_a || '',
        b: item.model_b || item.col_b || '',
        corr: corrVal
      });
    }
  });
  
  // 分析低覆盖率模型
  const lowCovModels = perf.filter(p => (p.coverage || 0) < 0.7);
  const highCovModels = perf.filter(p => (p.coverage || 0) >= 0.7);
  
  // 分析互补性强的模型对
  const topComp = (comp || []).slice(0, 5);
  const highCompPairs = topComp.filter(c => (c.complementarity || c.comp || 0) > 0.2);
  
  // 策略模拟结果
  const strategies = strategy.strategies || [];
  const withRescue = strategies.filter(s => (s.rescue_count || 0) > 0);
  
  // ── Step 2: 根据实际问题生成针对性建议（确保至少6条） ───────────────────────────────────
  
  // 【洞察1】整体性能问题
  if (avgAuc < 0.65 && avgKs < 0.25) {
    suggestions.push({
      type: 'danger',
      title: '🚨 模型整体性能偏弱',
      content: `当前 ${perf.length} 个模型平均 AUC=${avgAuc.toFixed(4)}、KS=${avgKs.toFixed(4)}，区分能力不足。需优先提升模型质量，而非急于部署策略。`,
      details: `建议：① 检查数据质量，是否存在标签噪声；② 增加有效特征变量；③ 考虑更换模型算法（如 XGBoost → LightGBM）`
    });
  } else if (avgAuc < 0.70 || avgKs < 0.30) {
    suggestions.push({
      type: 'warning',
      title: '⚠️ 模型性能有提升空间',
      content: `平均 AUC=${avgAuc.toFixed(4)}、KS=${avgKs.toFixed(4)}，基本可用但未达优秀。建议关注 KS 较高的模型作为主力。`,
      details: `当前最优：${topModel?.model || '未知'}（KS=${(topModel?.ks || 0).toFixed(4)}）`
    });
  } else {
    suggestions.push({
      type: 'success',
      title: '✅ 模型整体性能良好',
      content: `平均 AUC=${avgAuc.toFixed(4)}、KS=${avgKs.toFixed(4)}，模型质量满足风控需求，可以考虑部署。`,
      details: `Top3 模型：${top3Models.map(m => `${m.model}(KS=${(m.ks||0).toFixed(4)})`).join(' | ')}`
    });
  }
  
  // 【洞察2】高相关模型问题
  if (highCorrPairs.length > 0) {
    suggestions.push({
      type: 'warning',
      title: '🔗 发现高相关模型，需精简',
      content: `${highCorrPairs.length} 对模型相关性 > 0.85，保留多个高相关模型浪费计算资源且无额外增益。`,
      details: `建议保留其一：高相关对 ${highCorrPairs.slice(0, 3).map(p => `${p.a} ↔ ${p.b}`).join('、')}`
    });
  } else {
    suggestions.push({
      type: 'info',
      title: '✅ 模型间相关性适中',
      content: `模型间相关性处于合理范围，不存在严重的信息冗余，可以继续优化模型组合策略。`,
      details: `建议持续关注模型间的信息互补性`
    });
  }
  
  // 【洞察3】覆盖率问题
  if (lowCovModels.length > 0 && highCovModels.length > 0) {
    suggestions.push({
      type: 'info',
      title: '📶 覆盖率可形成互补',
      content: `${highCovModels.length} 个模型覆盖率充足（≥70%），${lowCovModels.length} 个偏低。可采用串行策略：先用高覆盖率筛选，再用低覆盖率的捞回。`,
      details: `高覆盖率：${highCovModels[0]?.model} | 低覆盖率：${lowCovModels[0]?.model}`
    });
  } else if (lowCovModels.length > 0) {
    suggestions.push({
      type: 'warning',
      title: '⚠️ 多模型覆盖率不足',
      content: `${lowCovModels.length} 个模型覆盖率 < 70%，可能导致部分客群无法评估。需评估数据缺失原因或优化模型。`,
      details: `低覆盖率模型：${lowCovModels.slice(0, 3).map(m => `${m.model}(${((m.coverage||0)*100).toFixed(1)}%)`).join('、')}`
    });
  } else {
    suggestions.push({
      type: 'success',
      title: '✅ 模型覆盖率充足',
      content: `所有模型覆盖率均 ≥70%，可以覆盖大部分客群，满足业务需求。`,
      details: `平均覆盖率：${(avgCov * 100).toFixed(1)}%`
    });
  }
  
  // 【洞察4】互补性与捞回潜力
  if (highCompPairs.length > 0) {
    const bestComp = highCompPairs[0];
    suggestions.push({
      type: 'strategy',
      title: '💡 发现高互补性组合',
      content: `${bestComp.model_a || bestComp.col_a} 与 ${bestComp.model_b || bestComp.col_b} 互补性强（${((bestComp.complementarity||0)*100).toFixed(1)}%），适合做捞回组合。`,
      details: `串行策略：先用主模型拒绝低分客群，再用该组合捞回部分高分被拒者`
    });
  } else if (comp && comp.length > 0) {
    suggestions.push({
      type: 'info',
      title: '📊 互补性一般',
      content: `当前模型间互补性较弱，捞回潜力有限。建议优先优化单模型质量。`,
      details: `Top互补性：${((comp[0]?.complementarity||0)*100).toFixed(1)}%`
    });
  }
  
  // 【洞察5】串行策略效果
  if (withRescue.length > 0) {
    const bestRescue = withRescue.sort((a, b) => (b.pass_rate || 0) - (a.pass_rate || 0))[0];
    const rescueIncrease = ((bestRescue.pass_rate - (strategies.find(s => !s.rescue_count)?.pass_rate || 0)) * 100).toFixed(1);
    const badRateIncrease = ((bestRescue.pass_bad_rate - (strategies.find(s => !s.rescue_count)?.pass_bad_rate || 0)) * 100).toFixed(2);
    
    if (parseFloat(badRateIncrease) < 2) {
      suggestions.push({
        type: 'success',
        title: '✅ 串行策略风险可控',
        content: `使用捞回模型可提升放款量，逾期率仅上升 ${badRateIncrease}pp，风险可控。`,
        details: `推荐组合：主模型 + ${bestRescue.rescue_model || '捞回模型'}`
      });
    } else {
      suggestions.push({
        type: 'warning',
        title: '⚠️ 串行策略需谨慎',
        content: `捞回后逾期率上升 ${badRateIncrease}pp，超出安全阈值。建议收紧捞回条件。`,
        details: `建议严格控制捞回比例`
      });
    }
  }
  
  // 【洞察6】逾期率风险评估
  if (avgBadRate > 0.15) {
    suggestions.push({
      type: 'danger',
      title: '🚨 逾期率偏高，需收紧风控',
      content: `当前整体逾期率 ${(avgBadRate * 100).toFixed(2)}%，超过安全阈值。建议收紧风控策略以控制风险。`,
      details: `措施：① 提高主模型 q 值；② 收紧捞回条件；③ 加强贷后监控；④ 增加反欺诈规则`
    });
  } else if (avgBadRate < 0.08) {
    suggestions.push({
      type: 'success',
      title: '✅ 逾期率控制良好',
      content: `当前逾期率 ${(avgBadRate * 100).toFixed(2)}% 处于健康水平，资产质量良好。`,
      details: `可以适当优化策略追求业务增长，但仍需保持风险警惕`
    });
  } else {
    suggestions.push({
      type: 'warning',
      title: '📈 逾期率处于中等水平',
      content: `当前逾期率 ${(avgBadRate * 100).toFixed(2)}% 处于可接受范围，建议持续监控并优化风控策略。`,
      details: `关注逾期率变化趋势，及时调整风控策略`
    });
  }
  
  // 【洞察7】欺诈检测建议
  if (topModel) {
    const欺诈Ability = (topModel?.auc || 0) < 0.60 ? '欺诈检测能力偏弱' : '欺诈检测能力尚可';
    suggestions.push({
      type: 'strategy',
      title: '🔍 欺诈检测策略建议',
      content: `${欺诈Ability}，建议加强以下措施：① 设备指纹识别；② 多头借贷检测；③ 欺诈名单库实时查询；④ 行为特征分析。`,
      details: `对于新客群，尤其需要关注欺诈风险`
    });
  }
  
  // 【洞察8】特征稳定性建议
  suggestions.push({
    type: 'info',
    title: '📉 特征稳定性监控建议',
    content: `建议对核心模型的特征进行 PSI 监控，及时发现特征漂移并重新训练模型。`,
    details: `特征稳定性是模型长期有效的关键，建议建立定期监控机制`
  });
  
  // 【洞察9】差异化定价建议
  if (avgBadRate > 0.05 && topModel) {
    suggestions.push({
      type: 'strategy',
      title: '💰 差异化风险定价建议',
      content: `基于当前逾期率水平，建议对不同风险等级的客户采用差异化定价策略。`,
      details: `高风险客户适用高定价，低风险客户可给予优惠以提升忠诚度和复借率`
    });
  }
  
  // 【洞察10】综合部署建议（仅在性能足够时给出）
  if (avgAuc >= 0.70 && avgKs >= 0.30 && topModel) {
    let deploymentStrategy = '';
    if (highCorrPairs.length > 0) {
      deploymentStrategy = `建议先精简高相关模型（保留其一），再部署串行策略。`;
    } else if (withRescue.length > 0) {
      deploymentStrategy = `推荐采用「主拒绝 + 差异化捞回」策略，选用 ${topModel.model} 作为主拒绝模型。`;
    } else {
      deploymentStrategy = `单模型策略即可满足需求，建议合理设定阈值区间。`;
    }
    suggestions.push({
      type: 'strategy',
      title: '🚀 部署建议',
      content: deploymentStrategy,
      details: `上线前需在测试集验证，建议灰度发布（10% → 30% → 50%）`
    });
  }
  
  return suggestions;
}

// 模型相关性Tab切换
function switchCorrTab(tab) {
  const tabs = ['performance', 'correlation', 'complement', 'strategy', 'roc'];
  tabs.forEach(t => {
    const panel = document.getElementById(`corr-panel-${t}`);
    const btn = document.getElementById(`btn-corr-${t}`);
    if (panel) {
      panel.classList.toggle('hidden', t !== tab);
    }
    if (btn) {
      btn.classList.toggle('btn-primary', t === tab);
      btn.classList.toggle('btn-secondary', t !== tab);
    }
  });
}

// 渲染性能总览
function renderCorrelationPerformance(data) {
  // KPI
  const container = document.getElementById('corr-summary-kpi');
  if (!container) return;
  
  const perf = data.performance || [];
  const avgAuc = perf.length > 0 ? perf.reduce((s, p) => s + (p.auc || 0), 0) / perf.length : 0;
  const avgKs = perf.length > 0 ? perf.reduce((s, p) => s + (p.ks || 0), 0) / perf.length : 0;
  const avgCov = perf.length > 0 ? perf.reduce((s, p) => s + (p.coverage || 0), 0) / perf.length : 0;
  
  container.innerHTML = `
    <div class="metric-card">
      <div class="metric-label">模型数量</div>
      <div class="metric-value">${perf.length}</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">平均AUC</div>
      <div class="metric-value">${avgAuc.toFixed(4)}</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">平均KS</div>
      <div class="metric-value">${avgKs.toFixed(4)}</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">平均覆盖率</div>
      <div class="metric-value">${(avgCov * 100).toFixed(1)}%</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">平均逾期率</div>
      <div class="metric-value">${((perf.reduce((s, p) => s + (p.bad_rate || 0), 0) / perf.length) * 100 || 0).toFixed(2)}%</div>
    </div>
  `;
  
  // 性能气泡图 - 适配后端键名
  const charts = data.charts || {};
  const perfImg = document.getElementById('img-corr-performance');
  if (perfImg) {
    // 尝试多种可能的键名
    const perfChartKey = charts['01_模型基础性能'] || charts['performance_bubble'] || charts['模型基础性能'];
    if (perfChartKey) {
      perfImg.src = 'data:image/png;base64,' + perfChartKey;
    }
  }
  
  // 性能明细表格
  const tableContainer = document.getElementById('corr-performance-table');
  if (!tableContainer) return;
  
  if (perf.length === 0) {
    tableContainer.innerHTML = '<p style="color:#9ca3af;text-align:center;">暂无性能数据</p>';
    return;
  }
  
  const rows = perf.map((p, i) => {
    const aucColor = (p.auc || 0) >= 0.75 ? '#10b981' : ((p.auc || 0) >= 0.65 ? '#f59e0b' : '#ef4444');
    const ksColor = (p.ks || 0) >= 0.35 ? '#10b981' : ((p.ks || 0) >= 0.25 ? '#f59e0b' : '#ef4444');
    return `
      <tr>
        <td>${i + 1}</td>
        <td title="${p.model || ''}">${(p.model || '未知').substring(0, 20)}</td>
        <td>${((p.coverage || 0) * 100).toFixed(1)}%</td>
        <td style="color:${aucColor};font-weight:bold;">${(p.auc || 0).toFixed(4)}</td>
        <td style="color:${ksColor};font-weight:bold;">${(p.ks || 0).toFixed(4)}</td>
        <td>${((p.bad_rate || 0) * 100).toFixed(2)}%</td>
        <td>${(p.n || 0).toLocaleString()}</td>
      </tr>
    `;
  }).join('');
  
  tableContainer.innerHTML = `
    <table class="data-table">
      <thead>
        <tr>
          <th>#</th>
          <th>模型名称</th>
          <th>覆盖率</th>
          <th>AUC</th>
          <th>KS</th>
          <th>逾期率</th>
          <th>样本数</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

// 渲染相关性图表
function renderCorrelationCharts(data) {
  const charts = data.charts || {};
  
  // 相关性热力图 - 适配后端键名
  const heatmapImg = document.getElementById('img-corr-heatmap');
  if (heatmapImg) {
    const heatmapKey = charts['02_相关性热力图'] || charts['correlation_heatmap'] || charts['相关性热力图'];
    if (heatmapKey) {
      heatmapImg.src = 'data:image/png;base64,' + heatmapKey;
    }
  }
  
  // 聚类树状图 - 适配后端键名
  const dendroImg = document.getElementById('img-corr-dendrogram');
  if (dendroImg) {
    const dendroKey = charts['03_聚类树状图'] || charts['clustering_dendrogram'] || charts['聚类树状图'];
    if (dendroKey) {
      dendroImg.src = 'data:image/png;base64,' + dendroKey;
    }
  }
}

// 渲染互补性矩阵
function renderCorrelationComplement(data) {
  const charts = data.charts || {};
  
  // 互补性矩阵图 - 适配后端键名
  const compImg = document.getElementById('img-corr-complement');
  if (compImg) {
    const compKey = charts['04_模型互补性矩阵'] || charts['complementarity_matrix'] || charts['模型互补性矩阵'];
    if (compKey) {
      compImg.src = 'data:image/png;base64,' + compKey;
    }
  }
}

// 渲染串行策略
function renderCorrelationStrategy(data) {
  const charts = data.charts || {};
  const strategyMetrics = data.strategy_metrics || {};
  
  // 串行策略图 - 适配后端键名
  const stratImg = document.getElementById('img-corr-strategy');
  if (stratImg) {
    const stratKey = charts['05_串行策略效果'] || charts['serial_strategy'] || charts['串行策略效果'];
    if (stratKey) {
      stratImg.src = 'data:image/png;base64,' + stratKey;
    }
  }
  
  // 策略明细表格
  const tableContainer = document.getElementById('corr-strategy-table');
  if (!tableContainer) return;
  
  // 使用后端返回的HTML表格
  if (data.strategy_table_html) {
    tableContainer.innerHTML = data.strategy_table_html;
    return;
  }
  
  // 备选：手动构建表格
  const perf = data.performance || [];
  if (perf.length === 0) {
    tableContainer.innerHTML = '<p style="color:#9ca3af;text-align:center;">暂无策略数据</p>';
    return;
  }
  
  // 按KS排序
  const sorted = [...perf].sort((a, b) => (b.ks || 0) - (a.ks || 0));
  
  let rows = '';
  let cumRecall = 0;
  let cumBad = 0;
  let cumTotal = 0;
  
  sorted.forEach((p, i) => {
    const recall = (p.coverage || 0) * (1 - (p.bad_rate || 0));
    cumRecall += recall;
    cumBad += (p.n || 0) * (p.bad_rate || 0);
    cumTotal += (p.n || 0);
    
    rows += `
      <tr>
        <td>${i + 1}</td>
        <td title="${p.model || ''}">${(p.model || '未知').substring(0, 15)}</td>
        <td>${((p.coverage || 0) * 100).toFixed(1)}%</td>
        <td>${(cumRecall * 100).toFixed(2)}%</td>
        <td>${((cumBad / cumTotal) * 100 || 0).toFixed(2)}%</td>
        <td>${cumTotal.toLocaleString()}</td>
      </tr>
    `;
  });
  
  tableContainer.innerHTML = `
    <table class="data-table">
      <thead>
        <tr>
          <th>顺序</th>
          <th>模型</th>
          <th>覆盖率</th>
          <th>累计召回</th>
          <th>累计逾期率</th>
          <th>累计样本</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

// 渲染ROC对比
function renderCorrelationRoc(data) {
  const charts = data.charts || {};
  
  // ROC曲线 - 适配后端键名
  const rocImg = document.getElementById('img-corr-roc');
  if (rocImg) {
    const rocKey = charts['06_ROC曲线对比'] || charts['roc_comparison'] || charts['ROC曲线对比'];
    if (rocKey) {
      rocImg.src = 'data:image/png;base64,' + rocKey;
    }
  }
  
  // 分数分布 - 适配后端键名
  const distImg = document.getElementById('img-corr-dist');
  if (distImg) {
    const distKey = charts['07_分数分布'] || charts['score_distribution'] || charts['分数分布'];
    if (distKey) {
      distImg.src = 'data:image/png;base64,' + distKey;
    }
  }
}

// ── 渲染专家指标卡片 ──────────────────────────────────────────────
function renderExpertMetrics(data, expertReports) {
  const da = expertReports.data_analyst || {};
  const me = expertReports.model_engineer || {};

  // 数据分析师的指标
  const metrics = da.metrics || {};
  const totalCount = metrics.total_count || data.n_samples || 0;
  const badCount = metrics.bad_count || 0;
  const badRate = metrics.bad_rate || 0;
  const ks = metrics.ks || 0;
  const auc = metrics.auc || 0;
  const psi = metrics.psi || 0;

  // 填充顶部指标卡片
  document.getElementById('metric-total').textContent = totalCount.toLocaleString();
  document.getElementById('metric-bad').textContent = badCount.toLocaleString();
  document.getElementById('metric-bad-rate').textContent = (badRate * 100).toFixed(2) + '%';
  document.getElementById('metric-ks').textContent = ks.toFixed(4);
  document.getElementById('metric-auc').textContent = auc.toFixed(4);
  document.getElementById('metric-psi').textContent = psi.toFixed(4);

  // 设置指标颜色
  setMetricColor('metric-ks', ks, 0.35, 0.25);
  setMetricColor('metric-auc', auc, 0.75, 0.65);
  setMetricColor('metric-psi', psi, 0.1, 0.25, true);

  // 渲染分箱表格
  renderExpertBinsTable(da.bins || []);

  // 渲染专家指标网格
  renderExpertMetricsGrid(expertReports);
}

function renderExpertMetricsGrid(expertReports) {
  const container = document.getElementById('expert-metrics-grid');
  if (!container) return;

  const da = expertReports.data_analyst || {};
  const me = expertReports.model_engineer || {};
  const rs = expertReports.risk_strategist || {};

  // 数据分析师指标
  const daMetrics = da.metrics || {};

  // 建模师指标
  const modelPerf = me.model_performance || [];

  // 构建HTML
  let html = `
    <div class="metric-card">
      <div class="metric-label">总样本数</div>
      <div class="metric-value">${(daMetrics.total_count || 0).toLocaleString()}</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">坏样本数</div>
      <div class="metric-value">${(daMetrics.bad_count || 0).toLocaleString()}</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">逾期率</div>
      <div class="metric-value ${(daMetrics.bad_rate || 0) > 0.05 ? 'warning' : 'success'}">${((daMetrics.bad_rate || 0) * 100).toFixed(2)}%</div>
    </div>
  `;

  // 模型性能（Top 3）
  if (modelPerf.length > 0) {
    modelPerf.slice(0, 3).forEach((m, i) => {
      const ksColor = m.ks >= 0.35 ? 'success' : (m.ks >= 0.25 ? 'warning' : 'danger');
      const aucColor = m.auc >= 0.75 ? 'success' : (m.auc >= 0.65 ? 'warning' : 'danger');
      html += `
        <div class="metric-card">
          <div class="metric-label">${m.model || `模型${i+1}`} KS</div>
          <div class="metric-value ${ksColor}">${(m.ks || 0).toFixed(4)}</div>
        </div>
        <div class="metric-card">
          <div class="metric-label">${m.model || `模型${i+1}`} AUC</div>
          <div class="metric-value ${aucColor}">${(m.auc || 0).toFixed(4)}</div>
        </div>
      `;
    });
  }

  container.innerHTML = html;
}

function renderExpertBinsTable(bins, modelName = '') {
  const tbody = document.getElementById('expert-bins-tbody');
  if (!tbody) return;
  
  // 更新模型名称显示
  const modelNameEl = document.getElementById('best-model-name');
  if (modelNameEl) {
    if (modelName) {
      modelNameEl.textContent = `当前展示模型: ${modelName}`;
    } else {
      modelNameEl.textContent = '';
    }
  }

  if (!bins || bins.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:#9ca3af;">暂无分箱数据</td></tr>';
    return;
  }

  let cumulativeBad = 0;
  let cumulativeTotal = 0;

  tbody.innerHTML = bins.map((bin, idx) => {
    cumulativeBad += bin.bad_count || 0;
    cumulativeTotal += bin.count || 0;
    const cumulativeRate = cumulativeTotal > 0 ? cumulativeBad / cumulativeTotal : 0;
    const badRate = bin.bad_rate || 0;

    // 颜色判断
    let rateColor = 'good';
    if (badRate > 0.08) rateColor = 'danger';
    else if (badRate > 0.04) rateColor = 'warning';

    // 累计逾期率颜色判断
    let cumColor = 'good';
    if (cumulativeRate > 0.08) cumColor = 'danger';
    else if (cumulativeRate > 0.04) cumColor = 'warning';
    
    return `
      <tr>
        <td>${idx + 1}</td>
        <td>${(bin.score_min || 0).toFixed(4)}</td>
        <td>${(bin.score_max || 0).toFixed(4)}</td>
        <td>${(bin.count || 0).toLocaleString()}</td>
        <td>${(bin.bad_count || 0).toLocaleString()}</td>
        <td>
          <div class="bin-progress">
            <div class="bin-bar-container">
              <div class="bin-bar ${rateColor}" style="width: ${Math.min(badRate * 20, 100)}%"></div>
            </div>
            <span class="bin-percent">${(badRate * 100).toFixed(2)}%</span>
          </div>
        </td>
        <td>
          <div class="bin-progress">
            <div class="bin-bar-container">
              <div class="bin-bar ${cumColor}" style="width: ${Math.min(cumulativeRate * 20, 100)}%"></div>
            </div>
            <span class="bins-cumulative">${(cumulativeRate * 100).toFixed(2)}%</span>
          </div>
        </td>
      </tr>
    `;
  }).join('');
}

// ── 渲染专家图表 ─────────────────────────────────────────────────
function renderExpertCharts(expertReports, data) {
  const da = expertReports.data_analyst || {};
  const me = expertReports.model_engineer || {};

  // 存储当前图表实例
  window.expertCharts = window.expertCharts || {};

  // 渲染Top6模型卡片
  renderTop6ModelsGrid(me.model_performance || [], me.bin_results || []);

  // 渲染最佳模型的分箱表格
  const bestModelBins = me.bin_results && me.bin_results.length > 0 
    ? me.bin_results[0].bins || []  // 第一个是AUC最高的
    : (da.bins || []);
  renderExpertBinsTable(bestModelBins, me.bin_results && me.bin_results[0] ? me.bin_results[0].model : '');

  // 渲染Top6模型分箱逾期率对比图
  renderTop6BadRateChart(me.bin_results || []);

  // 渲染特征重要性图
  renderFeatureImportanceChart(da.feature_importance || []);

  // 渲染模型对比图
  renderModelComparisonChart(me.model_performance || []);

  // 渲染相关性热力图
  renderCorrelationHeatmap(me.correlation_matrix || {}, me.model_performance || []);
}

// ── 渲染Top6模型卡片 ────────────────────────────────────────────
function renderTop6ModelsGrid(modelPerf, binResults) {
  const container = document.getElementById('top6-models-grid');
  if (!container) return;

  // 按AUC排序取前6个
  const sortedModels = [...modelPerf].sort((a, b) => (b.auc || 0) - (a.auc || 0)).slice(0, 6);
  
  if (sortedModels.length === 0) {
    container.innerHTML = '<p style="color:#9ca3af;text-align:center;padding:20px;">暂无模型数据</p>';
    return;
  }

  const colors = ['#2563eb', '#059669', '#d97706', '#dc2626', '#7c3aed', '#0891b2'];
  
  container.innerHTML = sortedModels.map((m, i) => {
    const isBest = i === 0;
    const ksColor = (m.ks || 0) >= 0.35 ? 'success' : ((m.ks || 0) >= 0.25 ? 'warning' : 'danger');
    const aucColor = (m.auc || 0) >= 0.75 ? 'success' : ((m.auc || 0) >= 0.65 ? 'warning' : 'danger');
    const borderStyle = isBest ? 'border:2px solid #10b981;' : 'border:1px solid #e5e7eb;';
    const badge = isBest ? '<span style="background:#10b981;color:#fff;padding:2px 6px;border-radius:4px;font-size:0.7rem;">BEST</span>' : '';
    const modelName = m.model || `模型${i+1}`;
    
    return `
      <div class="metric-card" style="${borderStyle}position:relative;" title="${modelName}">
        ${badge}
        <div class="metric-label" style="font-size:0.75rem;margin-top:${isBest ? '8px' : '0'};word-break:break-all;">${truncateName(modelName, 16)}</div>
        <div class="metric-value ${aucColor}" style="font-size:1.1rem;">${(m.auc || 0).toFixed(4)}</div>
        <div class="metric-sub" style="font-size:0.7rem;">AUC</div>
        <div class="metric-value ${ksColor}" style="font-size:0.95rem;">${(m.ks || 0).toFixed(4)}</div>
        <div class="metric-sub" style="font-size:0.7rem;">KS</div>
      </div>
    `;
  }).join('');
}

// 截断长名称（增加默认长度）
function truncateName(name, maxLen = 20) {
  if (!name) return '未知';
  if (name.length <= maxLen) return name;
  return name.substring(0, maxLen) + '...';
}

// ── Top6模型分箱逾期率对比图 ─────────────────────────────────────
function renderTop6BadRateChart(binResults) {
  const ctx = document.getElementById('chart-top6-badrate');
  if (!ctx) return;

  if (window.expertCharts.top6Badrate) {
    window.expertCharts.top6Badrate.destroy();
  }

  if (!binResults || binResults.length === 0) {
    ctx.parentElement.innerHTML = '<p style="color:#9ca3af;text-align:center;padding:60px;">暂无分箱数据</p>';
    return;
  }

  // 取前6个模型（已按AUC排序）
  const top6 = binResults.slice(0, 6);
  
  // 获取最大分箱数
  const maxBins = Math.max(...top6.map(m => (m.bins || []).length));
  const labels = Array.from({length: maxBins}, (_, i) => `箱${i + 1}`);
  
  // 颜色配置
  const colors = [
    { bg: 'rgba(37, 99, 235, 0.6)', border: 'rgba(37, 99, 235, 1)' },
    { bg: 'rgba(5, 150, 105, 0.6)', border: 'rgba(5, 150, 105, 1)' },
    { bg: 'rgba(217, 119, 6, 0.6)', border: 'rgba(217, 119, 6, 1)' },
    { bg: 'rgba(220, 38, 38, 0.6)', border: 'rgba(220, 38, 38, 1)' },
    { bg: 'rgba(124, 58, 237, 0.6)', border: 'rgba(124, 58, 237, 1)' },
    { bg: 'rgba(8, 145, 178, 0.6)', border: 'rgba(8, 145, 178, 1)' },
  ];

  // 构建数据集
  const datasets = top6.map((m, i) => ({
    label: `${truncateName(m.model || `M${i+1}`, 8)} (AUC=${(m.auc || 0).toFixed(3)})`,
    data: (m.bins || []).map(b => ((b.bad_rate || 0) * 100).toFixed(2) * 1),
    borderColor: colors[i].border,
    backgroundColor: colors[i].bg,
    fill: false,
    tension: 0.3,
    pointRadius: 3,
    pointHoverRadius: 5,
  }));

  window.expertCharts.top6Badrate = new Chart(ctx, {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { 
          position: 'bottom',
          labels: { boxWidth: 12, font: { size: 11 } }
        },
        tooltip: {
          callbacks: {
            label: (ctx) => `${ctx.dataset.label}: ${ctx.raw.toFixed(2)}%`
          }
        }
      },
      scales: {
        y: { 
          beginAtZero: true, 
          title: { display: true, text: '逾期率(%)' },
          ticks: { callback: (v) => v.toFixed(1) + '%' }
        },
        x: { title: { display: true, text: '分箱序号' } }
      }
    }
  });

  // 渲染图例
  const legendContainer = document.getElementById('top6-legend');
  if (legendContainer) {
    legendContainer.innerHTML = top6.map((m, i) => `
      <span style="display:inline-flex;align-items:center;gap:4px;padding:4px 8px;background:${colors[i].bg};border-radius:4px;font-size:0.8rem;color:#fff;">
        ${truncateName(m.model || `M${i+1}`, 10)}: AUC=${(m.auc || 0).toFixed(3)}
      </span>
    `).join('');
  }
}

// 分箱样本分布图
function renderBinsDistributionChart(bins) {
  const ctx = document.getElementById('chart-bins-distribution');
  if (!ctx) return;

  if (window.expertCharts.distribution) {
    window.expertCharts.distribution.destroy();
  }

  const labels = bins.map((_, i) => `箱${i + 1}`);
  const counts = bins.map(b => b.count || 0);
  const badCounts = bins.map(b => b.bad_count || 0);

  window.expertCharts.distribution = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: labels,
      datasets: [
        {
          label: '样本数',
          data: counts,
          backgroundColor: 'rgba(37, 99, 235, 0.7)',
          borderColor: 'rgba(37, 99, 235, 1)',
          borderWidth: 1,
        },
        {
          label: '坏样本数',
          data: badCounts,
          backgroundColor: 'rgba(239, 68, 68, 0.7)',
          borderColor: 'rgba(239, 68, 68, 1)',
          borderWidth: 1,
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: 'top' }
      },
      scales: {
        y: { beginAtZero: true }
      }
    }
  });
}

// 分箱逾期率趋势图
function renderBinsBadRateChart(bins) {
  const ctx = document.getElementById('chart-bins-badrate');
  if (!ctx) return;

  if (window.expertCharts.badrate) {
    window.expertCharts.badrate.destroy();
  }

  const labels = bins.map((_, i) => `箱${i + 1}`);
  const badRates = bins.map(b => ((b.bad_rate || 0) * 100).toFixed(2));

  window.expertCharts.badrate = new Chart(ctx, {
    type: 'line',
    data: {
      labels: labels,
      datasets: [{
        label: '逾期率(%)',
        data: badRates,
        borderColor: 'rgba(239, 68, 68, 1)',
        backgroundColor: 'rgba(239, 68, 68, 0.1)',
        fill: true,
        tension: 0.3,
        pointRadius: 4,
        pointBackgroundColor: 'rgba(239, 68, 68, 1)',
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: 'top' }
      },
      scales: {
        y: { beginAtZero: true, title: { display: true, text: '逾期率(%)' } }
      }
    }
  });
}

// 特征重要性图
function renderFeatureImportanceChart(features) {
  const ctx = document.getElementById('chart-feature-importance');
  if (!ctx || !features || features.length === 0) return;

  if (window.expertCharts.features) {
    window.expertCharts.features.destroy();
  }

  // 只取前10个
  const topFeatures = features.slice(0, 10).reverse();
  const labels = topFeatures.map(f => f.feature || '未知');
  const values = topFeatures.map(f => Math.abs(f.spearman_corr || 0));

  window.expertCharts.features = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: labels,
      datasets: [{
        label: 'Spearman相关系数',
        data: values,
        backgroundColor: 'rgba(16, 185, 129, 0.7)',
        borderColor: 'rgba(16, 185, 129, 1)',
        borderWidth: 1,
      }]
    },
    options: {
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false }
      },
      scales: {
        x: { 
          beginAtZero: true, 
          title: { display: true, text: '|Spearman相关系数|' },
          ticks: { maxRotation: 0 }
        },
        y: {
          ticks: { 
            font: { size: 11 },
            // 截断过长的标签，保留前后各8个字符
            callback: function(value) {
              const label = this.getLabelForValue(value);
              if (label.length > 18) {
                return label.substring(0, 8) + '...' + label.substring(label.length - 8);
              }
              return label;
            }
          }
        }
      }
    }
  });
}

// 模型对比图
function renderModelComparisonChart(models) {
  const ctx = document.getElementById('chart-model-ksauc');
  if (!ctx || !models || models.length === 0) return;

  if (window.expertCharts.models) {
    window.expertCharts.models.destroy();
  }

  const labels = models.map(m => m.model || '未知');
  const ksValues = models.map(m => m.ks || 0);
  const aucValues = models.map(m => m.auc || 0);

  window.expertCharts.models = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: labels,
      datasets: [
        {
          label: 'KS',
          data: ksValues,
          backgroundColor: 'rgba(37, 99, 235, 0.7)',
          borderColor: 'rgba(37, 99, 235, 1)',
          borderWidth: 1,
        },
        {
          label: 'AUC',
          data: aucValues,
          backgroundColor: 'rgba(16, 185, 129, 0.7)',
          borderColor: 'rgba(16, 185, 129, 1)',
          borderWidth: 1,
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: 'top' }
      },
      scales: {
        y: { beginAtZero: true, max: 1 },
        x: {
          ticks: {
            font: { size: 11 },
            // X轴标签旋转45度，避免重叠
            maxRotation: 45,
            minRotation: 0,
            // 截断过长标签
            callback: function(value) {
              const label = this.getLabelForValue(value);
              if (label.length > 15) {
                return label.substring(0, 12) + '...';
              }
              return label;
            }
          }
        }
      }
    }
  });
}

// 相关性热力图
function renderCorrelationHeatmap(corMatrix, models) {
  const container = document.getElementById('chart-correlation-heatmap');
  if (!container || !models || models.length < 2) {
    if (container) container.innerHTML = '<p style="color:#9ca3af;text-align:center;padding:60px;">模型数量不足2个，无法生成相关性热力图</p>';
    return;
  }

  const modelNames = models.map(m => m.model || '未知');

  // 生成HTML热力图
  let html = '<div style="display:flex;flex-direction:column;gap:2px;">';

  // 表头
  html += '<div style="display:flex;gap:2px;">';
  html += '<div style="width:100px;"></div>';
  modelNames.forEach(name => {
    html += `<div class="heatmap-label" style="width:100px;text-align:center;font-size:0.7rem;" title="${name}">${name.substring(0, 15)}</div>`;
  });
  html += '</div>';

  // 行
  modelNames.forEach((rowName, i) => {
    html += '<div style="display:flex;gap:2px;">';
    html += `<div class="heatmap-label" style="width:100px;font-size:0.7rem;" title="${rowName}">${rowName.substring(0, 15)}</div>`;
    modelNames.forEach((colName, j) => {
      const val = i === j ? 1 : (corMatrix[rowName]?.[colName] || 0);
      const color = getHeatmapColor(val);
      html += `<div class="heatmap-cell" style="width:100px;background:${color};color:${Math.abs(val) > 0.5 ? 'white' : '#333'};" title="${rowName} ↔ ${colName}: ${val.toFixed(3)}">${val.toFixed(2)}</div>`;
    });
    html += '</div>';
  });

  html += '</div>';
  container.innerHTML = html;
}

function getHeatmapColor(value) {
  // 从蓝(负相关)到白(0)到红(正相关)
  if (value >= 0) {
    const intensity = Math.min(value, 1);
    const r = Math.round(255 * intensity);
    const g = Math.round(255 * (1 - intensity * 0.5));
    const b = Math.round(255 * (1 - intensity));
    return `rgb(${r}, ${g}, ${b})`;
  } else {
    const intensity = Math.min(Math.abs(value), 1);
    const r = Math.round(255 * (1 - intensity * 0.5));
    const g = Math.round(255 * (1 - intensity));
    const b = Math.round(255 * intensity);
    return `rgb(${r}, ${g}, ${b})`;
  }
}

// 图表切换
function switchChartTab(tab) {
  const panels = ['metrics', 'bins', 'features', 'models'];
  panels.forEach(p => {
    const panel = document.getElementById(`chart-panel-${p}`);
    const btn = document.getElementById(`btn-chart-${p}`);
    if (panel) {
      panel.classList.toggle('hidden', p !== tab);
    }
    if (btn) {
      btn.classList.toggle('btn-primary', p === tab);
      btn.classList.toggle('btn-secondary', p !== tab);
    }
  });
}

// 格式化AI建议文本，按段落分割
function formatAIAdvice(text, maxChars = 150) {
  if (!text) return '<span style="color:#9ca3af;">（分析中...）</span>';
  
  // 移除Markdown标题格式，转为HTML
  let formatted = text
    .replace(/^### (.+)$/gm, '<strong>$1</strong>')
    .replace(/^## (.+)$/gm, '<strong>$1</strong>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\n\n+/g, '<br><br>')
    .replace(/\n/g, '<br>');
  
  const truncated = formatted.length > maxChars 
    ? formatted.substring(0, maxChars) + '...'
    : formatted;
  
  return truncated;
}

function renderExpertReport(data) {
  const container = document.getElementById('suggestions-list');
  container.innerHTML = '';

  const expertReports = data.expert_reports || {};

  // 1. 数据分析师报告（极简版）
  if (expertReports.data_analyst) {
    const da = expertReports.data_analyst;
    const metrics = da.metrics || {};
    const features = da.feature_importance || [];
    const statusBadge = (metrics.bad_rate || 0) > 0.05 
      ? '<span style="background:#fef3c7;color:#d97706;padding:2px 8px;border-radius:4px;font-size:0.75rem;">⚠️ 逾期率偏高</span>'
      : '<span style="background:#d1fae5;color:#059669;padding:2px 8px;border-radius:4px;font-size:0.75rem;">✅ 正常</span>';

    const card = document.createElement('div');
    card.className = 'expert-card';
    card.style.cssText = 'border-left:3px solid #3b82f6;margin-bottom:12px;';
    card.innerHTML = `
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;">
        <span style="font-size:1.2rem;">📊</span>
        <strong style="font-size:0.95rem;">数据分析师诊断</strong>
        ${statusBadge}
      </div>
      <div style="display:flex;gap:16px;margin-bottom:10px;">
        <div style="text-align:center;">
          <div style="font-size:1.1rem;font-weight:bold;color:${(metrics.bad_rate || 0) > 0.05 ? '#d97706' : '#059669'};">${((metrics.bad_rate || 0) * 100).toFixed(2)}%</div>
          <div style="font-size:0.7rem;color:#6b7280;">逾期率</div>
        </div>
        <div style="text-align:center;">
          <div style="font-size:1.1rem;font-weight:bold;color:${(metrics.ks || 0) >= 0.35 ? '#059669' : '#d97706'};">${(metrics.ks || 0).toFixed(4)}</div>
          <div style="font-size:0.7rem;color:#6b7280;">KS</div>
        </div>
        <div style="text-align:center;">
          <div style="font-size:1.1rem;font-weight:bold;color:${(metrics.auc || 0) >= 0.75 ? '#059669' : '#d97706'};">${(metrics.auc || 0).toFixed(4)}</div>
          <div style="font-size:0.7rem;color:#6b7280;">AUC</div>
        </div>
      </div>
      ${features.length > 0 ? `
        <div style="margin-bottom:8px;">
          <span style="font-size:0.75rem;color:#6b7280;">Top3风险特征：</span>
          ${features.slice(0, 3).map(f =>
            `<span style="display:inline-block;background:#fee2e2;color:#dc2626;padding:2px 6px;border-radius:3px;margin:2px;font-size:0.7rem;">${truncateName(f.feature, 8)}</span>`
          ).join('')}
        </div>
      ` : ''}
      <div style="font-size:0.8rem;color:#4b5563;line-height:1.5;padding:8px;background:#f8fafc;border-radius:4px;">
        ${formatAIAdvice(da.diagnosis, 200)}
        ${(da.diagnosis || '').length > 200 ? `<a href="javascript:void(0)" onclick="toggleExpertAdvice(this, 'da')" style="color:#3b82f6;">展开</a>` : ''}
        <div class="expert-advice-full hidden" style="margin-top:8px;">${formatAIAdvice(da.diagnosis, 9999)}</div>
      </div>
    `;
    container.appendChild(card);
  }

  // 2. 建模师评估（极简版，图表区域已展示模型）
  if (expertReports.model_engineer) {
    const me = expertReports.model_engineer;
    const modelPerf = me.model_performance || [];
    
    const bestModel = [...modelPerf].sort((a, b) => (b.auc || 0) - (a.auc || 0))[0];

    const card = document.createElement('div');
    card.className = 'expert-card';
    card.style.cssText = 'border-left:3px solid #7c3aed;margin-bottom:12px;';
    card.innerHTML = `
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;">
        <span style="font-size:1.2rem;">🤖</span>
        <strong style="font-size:0.95rem;">金融建模师评估</strong>
        ${bestModel ? `<span style="background:#eff6ff;color:#2563eb;padding:2px 8px;border-radius:4px;font-size:0.7rem;">最优: ${truncateName(bestModel.model, 10)}</span>` : ''}
      </div>
      <div style="font-size:0.8rem;color:#4b5563;line-height:1.5;padding:8px;background:#f8fafc;border-radius:4px;">
        ${formatAIAdvice(me.evaluation, 200)}
        ${(me.evaluation || '').length > 200 ? `<a href="javascript:void(0)" onclick="toggleExpertAdvice(this, 'me')" style="color:#3b82f6;">展开</a>` : ''}
        <div class="expert-advice-full hidden" style="margin-top:8px;">${formatAIAdvice(me.evaluation, 9999)}</div>
      </div>
    `;
    container.appendChild(card);
  }

  // 3. 策略专家建议（极简版，重点突出）
  if (expertReports.risk_strategist) {
    const rs = expertReports.risk_strategist;

    const card = document.createElement('div');
    card.className = 'expert-card';
    card.style.cssText = 'border-left:3px solid #10b981;margin-bottom:12px;background:#f0fdf4;';
    card.innerHTML = `
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;">
        <span style="font-size:1.2rem;">🎯</span>
        <strong style="font-size:0.95rem;">风控策略专家建议</strong>
      </div>
      <div style="font-size:0.85rem;color:#1f2937;line-height:1.6;padding:10px;background:#fff;border-radius:4px;border:1px solid #d1fae5;">
        ${formatAIAdvice(rs.strategy_advice, 300)}
        ${(rs.strategy_advice || '').length > 300 ? `<a href="javascript:void(0)" onclick="toggleExpertAdvice(this, 'rs')" style="color:#10b981;">展开</a>` : ''}
        <div class="expert-advice-full hidden" style="margin-top:8px;">${formatAIAdvice(rs.strategy_advice, 9999)}</div>
      </div>
    `;
    container.appendChild(card);
  }

  // 如果没有内容
  if (Object.keys(expertReports).length === 0) {
    container.innerHTML = '<p style="color:#9ca3af;text-align:center;padding:30px;">专家分析正在进行中，请稍候...</p>';
  }
}

function toggleExpertAdvice(link, type) {
  const card = link.closest('.expert-card');
  const fullDiv = card.querySelector('.expert-advice-full');
  if (fullDiv) {
    fullDiv.classList.toggle('hidden');
    link.textContent = fullDiv.classList.contains('hidden') ? '展开' : '收起';
  }
}

// 旧的toggleExpertDiagnosis已被toggleExpertAdvice替代

function createExpertCard(icon, title, content) {
  const card = document.createElement('div');
  card.className = 'suggestion-item info';
  card.style.marginBottom = '16px';
  card.style.borderLeft = '4px solid #3b82f6';

  // 处理内容，保留格式但截断过长部分
  const paragraphs = content.split(/\n\n+/).filter(p => p.trim());
  const displayContent = paragraphs.slice(0, 5).join('\n\n');
  const hasMore = paragraphs.length > 5;

  card.innerHTML = `
    <div class="suggestion-header">
      <span class="expert-category">${icon} ${title}</span>
      ${hasMore ? '<button class="btn btn-sm" style="margin-left:auto;">展开全部</button>' : ''}
    </div>
    <div class="suggestion-content" style="margin-top:12px;font-size:13px;line-height:1.7;">
      <pre style="white-space:pre-wrap;word-wrap:break-word;font-family:inherit;">${escapeHtml(displayContent)}${hasMore ? '\n\n...' : ''}</pre>
    </div>
  `;

  // 添加展开功能
  if (hasMore) {
    const btn = card.querySelector('.btn');
    const content = card.querySelector('.suggestion-content pre');
    btn.onclick = () => {
      if (content.textContent.endsWith('...')) {
        content.textContent = content.textContent.replace(/\n\n\.\.\.$/, '');
        btn.textContent = '收起';
      } else {
        content.textContent = displayContent + '\n\n...';
        btn.textContent = '展开全部';
      }
    };
  }

  return card;
}

function addDownloadButton(data) {
  // 添加下载报告按钮到操作区域
  const actionArea = document.querySelector('#analysis-result .card:last-of-type');
  if (actionArea) {
    // 检查是否已有下载按钮
    if (!document.getElementById('btn-download-report')) {
      const downloadBtn = document.createElement('button');
      downloadBtn.id = 'btn-download-report';
      downloadBtn.className = 'btn btn-secondary';
      downloadBtn.innerHTML = '<span>📥</span> 下载分析报告';
      downloadBtn.style.marginLeft = '12px';
      downloadBtn.onclick = () => downloadExpertReport(data.task_id);

      const saveBtn = document.getElementById('btn-save-record');
      if (saveBtn) {
        saveBtn.parentNode.insertBefore(downloadBtn, saveBtn.nextSibling);
      }
    }
  }
}

function toggleReport() {
  const reportDiv = document.getElementById('expert-full-report');
  if (reportDiv) {
    reportDiv.classList.toggle('hidden');
  }
}

async function downloadExpertReport(taskId) {
  if (!taskId) {
    showToast('无法下载：缺少任务ID', 'error');
    return;
  }

  try {
    // 直接打开下载链接
    window.open(`${API_BASE}/analysis/report/${taskId}`, '_blank');
    showToast('报告下载中...', 'success');
  } catch (err) {
    showToast('下载失败: ' + err.message, 'error');
  }
}

function displayResults(data) {
  const result = data.result;
  const suggestions = data.suggestion;
  const expertReports = data.expert_reports || {};
  const finalReport = data.final_report || '';

  // 显示结果区域
  document.getElementById('analysis-result').classList.remove('hidden');

  // 切换：显示单模型视图，隐藏多模型视图
  document.getElementById('result-bins-card').classList.remove('hidden');
  document.getElementById('result-multi-model-card').classList.add('hidden');

  // 汇总指标
  const summary = result.summary;
  document.getElementById('metric-total').textContent = summary.total_count.toLocaleString();
  document.getElementById('metric-bad').textContent = summary.bad_count.toLocaleString();
  document.getElementById('metric-bad-rate').textContent = (summary.bad_rate * 100).toFixed(2) + '%';

  // 模型指标
  const metrics = result.metrics;
  document.getElementById('metric-ks').textContent = metrics.ks.toFixed(4);
  document.getElementById('metric-auc').textContent = metrics.auc.toFixed(4);
  document.getElementById('metric-psi').textContent = metrics.psi.toFixed(4);

  // 设置指标颜色
  setMetricColor('metric-ks', metrics.ks, 0.35, 0.25);
  setMetricColor('metric-auc', metrics.auc, 0.75, 0.65);
  setMetricColor('metric-psi', metrics.psi, 0.1, 0.25, true);

  // 分箱表格
  renderBinsTable(result.bins);

  // 多专家分析结果（新增）
  if (data.mode === 'multi_expert' && finalReport) {
    renderMultiExpertReport(expertReports, finalReport);
  } else {
    renderSuggestions(suggestions);
  }
}

function renderMultiExpertReport(expertReports, finalReport) {
  const container = document.getElementById('suggestions-list');
  container.innerHTML = '';
  
  // 如果有专家报告，按专家分组显示
  if (expertReports && Object.keys(expertReports).length > 0) {
    for (const [expertId, report] of Object.entries(expertReports)) {
      const card = document.createElement('div');
      card.className = 'suggestion-item info';
      card.style.marginBottom = '16px';
      card.style.borderLeft = '4px solid #3b82f6';
      
      const icon = report.icon || '📊';
      const name = report.name || expertId;
      const conclusion = report.conclusion || '（无分析结果）';
      
      // 将结论按段落分割，每段作为独立内容块
      const paragraphs = conclusion.split(/\n\n+/).filter(p => p.trim());
      const shortConclusion = paragraphs.slice(0, 3).join('<br><br>'); // 只显示前3段
      
      card.innerHTML = `
        <div class="suggestion-header">
          <span class="suggestion-category">${icon} ${name}</span>
        </div>
        <div class="suggestion-content" style="max-height: 200px; overflow: hidden; position: relative;">
          ${shortConclusion}
          ${paragraphs.length > 3 ? '<div style="position:absolute;bottom:0;left:0;right:0;height:40px;background:linear-gradient(transparent,#fff);"></div>' : ''}
        </div>
        ${paragraphs.length > 3 ? `<button class="btn btn-sm" onclick="this.previousElementSibling.style.maxHeight='none';this.previousElementSibling.querySelector('div:last-child').style.display='none';this.remove()" style="margin-top:8px;">展开全部</button>` : ''}
      `;
      container.appendChild(card);
    }
  }
  
  // 添加完整的最终报告链接或展开区域
  if (finalReport) {
    const reportCard = document.createElement('div');
    reportCard.className = 'suggestion-item warning';
    reportCard.style.marginTop = '16px';
    reportCard.style.borderLeft = '4px solid #f59e0b';
    reportCard.innerHTML = `
      <div class="suggestion-header">
        <span class="suggestion-category">📋 综合分析报告</span>
        <button class="btn btn-sm" onclick="this.textContent=this.textContent==='展开报告'?'收起报告':'展开报告';document.getElementById('full-report-content').classList.toggle('hidden')" style="margin-left:auto;">展开报告</button>
      </div>
      <div id="full-report-content" class="hidden" style="margin-top:12px;padding:16px;background:#fff9e6;border-radius:8px;max-height:400px;overflow-y:auto;">
        <pre style="white-space:pre-wrap;word-wrap:break-word;font-size:13px;line-height:1.6;">${escapeHtml(finalReport)}</pre>
      </div>
    `;
    container.appendChild(reportCard);
  }
  
  // 如果没有内容，显示提示
  if ((!expertReports || Object.keys(expertReports).length === 0) && !finalReport) {
    container.innerHTML = '<p class="empty-desc">暂无分析建议</p>';
  }
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

function displayMultiModelResults(data) {
  // 显示结果区域
  document.getElementById('analysis-result').classList.remove('hidden');

  // 切换：隐藏单模型视图，显示多模型视图
  document.getElementById('result-bins-card').classList.add('hidden');
  document.getElementById('result-multi-model-card').classList.remove('hidden');

  // 元信息
  document.getElementById('multi-model-meta').textContent =
    `样本量：${(data.n_samples || 0).toLocaleString()}　|　模型数量：${data.n_models || 0}`;

  // ── KPI 指标卡（复用现有） ────────────────────────────────────────────
  const perf = data.performance || [];
  const summary = perf[0] || {};
  document.getElementById('metric-total').textContent = (data.n_samples || 0).toLocaleString();
  document.getElementById('metric-bad').textContent = perf.length > 0
    ? `${(summary.bad_rate * 100 || 0).toFixed(1)}%`
    : '-';
  document.getElementById('metric-bad-rate').textContent =
    `${(perf.reduce((s, r) => s + r.bad_rate, 0) / (perf.length || 1) * 100).toFixed(2)}%`;
  document.getElementById('metric-ks').textContent = summary.ks || '-';
  document.getElementById('metric-auc').textContent = summary.auc || '-';
  document.getElementById('metric-psi').textContent = '-'; // 多模型不展示单一 PSI

  setMetricColor('metric-ks', summary.ks, 0.35, 0.25);
  setMetricColor('metric-auc', summary.auc, 0.75, 0.65);

  // ── 嵌入 HTML 报告 ────────────────────────────────────────────────────
  const frame = document.getElementById('multi-model-report-frame');
  const htmlReport = data.html_report || '';
  frame.srcdoc = htmlReport;

  // 同步 iframe 高度
  frame.onload = () => {
    try {
      const h = frame.contentWindow.document.body.scrollHeight;
      frame.style.minHeight = Math.max(h + 40, 600) + 'px';
    } catch (_) {}
  };

  // ── 建议列表 ───────────────────────────────────────────────────────────
  renderSuggestions(data.suggestion || []);
}

function setMetricColor(id, value, good, warning, reverse = false) {
  const el = document.getElementById(id);
  el.classList.remove('success', 'warning', 'danger');
  
  if (reverse) {
    // PSI 越小越好
    if (value <= good) el.classList.add('success');
    else if (value <= warning) el.classList.add('warning');
    else el.classList.add('danger');
  } else {
    if (value >= good) el.classList.add('success');
    else if (value >= warning) el.classList.add('warning');
    else el.classList.add('danger');
  }
}

function renderBinsTable(bins) {
  const tbody = document.getElementById('bins-tbody');
  tbody.innerHTML = '';
  
    bins.forEach((bin, idx) => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${bin.bin_no !== undefined ? bin.bin_no : (idx + 1)}</td>
      <td>${(bin.score_min !== undefined ? bin.score_min : 0).toFixed(4)}</td>
      <td>${(bin.score_max !== undefined ? bin.score_max : 0).toFixed(4)}</td>
      <td>${bin.count.toLocaleString()}</td>
      <td>${bin.bad_count.toLocaleString()}</td>
      <td>${(bin.bad_rate * 100).toFixed(2)}%</td>
    `;
    tbody.appendChild(tr);
  });
}

// ── 渲染AI策略建议（统一版本，支持LLM富文本 + 规则引擎降级） ─────────────
// 注意：此函数定义在 1283 行，此处旧版已删除，避免重复定义覆盖新版

async function exportReport() {
  if (!currentState.analysisResult) {
    showToast('请先完成分析', 'error');
    return;
  }
  
  const taskId = currentState.analysisResult.task_id;
  window.open(`${API_BASE}/analysis/${taskId}/export`, '_blank');
}

function saveToRecord() {
  if (!currentState.analysisResult) {
    showToast('请先完成分析', 'error');
    return;
  }
  
  // 打开保存弹窗
  openSaveRecordModal();
}

// ── 记录页面 ─────────────────────────────────────────────────────────────────
function initRecordsPage() {
  document.getElementById('btn-new-record').addEventListener('click', () => {
    openRecordModal();
  });
  
  document.getElementById('btn-export-records').addEventListener('click', exportRecords);
  
  // 筛选
  document.getElementById('filter-type').addEventListener('change', loadRecords);
  document.getElementById('filter-status').addEventListener('change', loadRecords);
  document.getElementById('search-record').addEventListener('input', debounce(loadRecords, 300));
}

async function loadRecords() {
  const type = document.getElementById('filter-type').value;
  const status = document.getElementById('filter-status').value;
  const q = document.getElementById('search-record').value;
  
  try {
    const params = new URLSearchParams();
    if (type) params.append('type', type);
    if (status) params.append('status', status);
    if (q) params.append('q', q);
    
    const res = await fetch(`${API_BASE}/records?${params}`);
    const data = await res.json();
    
    currentState.records = data.records || [];
    renderRecordsTable(data.records);
  } catch (err) {
    showToast('加载记录失败', 'error');
  }
}

function renderRecordsTable(records) {
  const tbody = document.getElementById('records-tbody');
  tbody.innerHTML = '';
  
  if (!records || records.length === 0) {
    tbody.innerHTML = `
      <tr><td colspan="8" style="text-align:center;padding:40px;color:#9ca3af;">
        暂无策略调整记录
      </td></tr>
    `;
    return;
  }
  
  records.forEach(r => {
    const tr = document.createElement('tr');
    // 截取调整内容和备注的摘要（过长时显示前50个字符）
    const contentSummary = r.content && r.content.length > 50 
      ? r.content.substring(0, 50) + '...' 
      : (r.content || '-');
    const notesSummary = r.notes && r.notes.length > 50 
      ? r.notes.substring(0, 50) + '...' 
      : (r.notes || '-');
    
    tr.innerHTML = `
      <td>${r.strategy_name}</td>
      <td>${r.adjusted_at}</td>
      <td>${r.strategy_type}</td>
      <td><span class="badge badge-${r.review_status}">${getStatusText(r.review_status)}</span></td>
      <td>${r.expected_goal || '-'}</td>
      <td title="${r.content || ''}">${contentSummary}</td>
      <td title="${r.notes || ''}">${notesSummary}</td>
      <td>
        <button class="btn btn-sm btn-primary" onclick="openRecordModalForEdit(${r.id})">编辑</button>
        <button class="btn btn-sm btn-secondary" onclick="viewRecord(${r.id})">查看</button>
        ${r.review_status === 'pending' ? `<button class="btn btn-sm btn-warning" onclick="openReviewModal(${r.id})">复盘</button>` : ''}
      </td>
    `;
    tbody.appendChild(tr);
  });
}

function getStatusText(status) {
  const map = { pending: '待复盘', done: '已复盘' };
  return map[status] || status;
}

function exportRecords() {
  window.open(`${API_BASE}/records/export`, '_blank');
}

// ── 复盘页面 ─────────────────────────────────────────────────────────────────
function initReviewsPage() {
  // 筛选
  document.getElementById('filter-review-type').addEventListener('change', loadReviews);
  document.getElementById('filter-review-status').addEventListener('change', loadReviews);
  document.getElementById('search-review').addEventListener('input', debounce(loadReviews, 300));
}

async function loadReviews() {
  const q = document.getElementById('search-review')?.value || '';

  try {
    const params = new URLSearchParams();
    if (q) params.append('q', q);

    const res = await fetch(`${API_BASE}/reviews?${params}`);
    const data = await res.json();

    // 前端过滤（label/status 筛选用）
    let reviews = data.reviews || [];
    const typeFilter    = document.getElementById('filter-review-type')?.value    || '';
    const statusFilter  = document.getElementById('filter-review-status')?.value  || '';

    if (typeFilter) {
      reviews = reviews.filter(r => r.manual_label === typeFilter || (typeFilter === 'pending' && !r.manual_label));
    }
    if (statusFilter) {
      if (statusFilter === 'pending') reviews = reviews.filter(r => !r.review_result);
      else if (statusFilter === 'done') reviews = reviews.filter(r => !!r.review_result);
    }

    currentState.reviews = reviews;
    renderReviewsTable(reviews);
  } catch (err) {
    showToast('加载复盘记录失败', 'error');
  }
}

function renderReviewsTable(reviews) {
  const tbody = document.getElementById('reviews-tbody');
  tbody.innerHTML = '';

  if (!reviews || reviews.length === 0) {
    tbody.innerHTML = `
      <tr><td colspan="5" style="text-align:center;padding:40px;color:#9ca3af;">
        暂无复盘记录
      </td></tr>
    `;
    return;
  }

  reviews.forEach(r => {
    const result = r.review_result || [];
    const improvedCount = result.filter(x => x.improved).length;
    const totalCount = result.length;

    // 分析类型标签
    const analysisTagsRaw = r.record?.analysis_tags || r.record?.notes_tags || '';
    const analysisTags = analysisTagsRaw ? analysisTagsRaw.split(',').filter(Boolean) : [];
    const tagsHtml = analysisTags.length > 0
      ? analysisTags.map(t => `<span class="badge badge-pending" style="margin-right:3px;font-size:0.7rem;">${getAnalysisTagLabel(t)}</span>`).join('')
      : '<span style="color:#9ca3af;font-size:0.8rem;">-</span>';

    // 标注：可点击编辑
    const labelBadge = r.manual_label
      ? `<span class="badge badge-${r.manual_label}" style="cursor:pointer;" onclick="openLabelModal(${r.id}, '${r.manual_label}', \`${(r.manual_note || '').replace(/`/g, '\\`')}\`)">${getLabelText(r.manual_label)}</span>`
      : `<span class="badge badge-pending" style="cursor:pointer;" onclick="openLabelModal(${r.id}, '', '')">✏️ 待标注</span>`;

    // 心得预览（如果有的话）
    const notePreview = r.manual_note
      ? `<div style="font-size:0.75rem;color:#6b7280;margin-top:4px;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${r.manual_note}">📝 ${r.manual_note}</div>`
      : '';

    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${r.record?.strategy_name || '-'}</td>
      <td>${r.review_date}</td>
      <td>${tagsHtml}</td>
      <td>${improvedCount}/${totalCount} 项改善</td>
      <td>
        <div>${labelBadge}</div>
        ${notePreview}
      </td>
    `;
    tbody.appendChild(tr);
  });
}

function getAnalysisTagLabel(value) {
  const tagMap = {
    'model_score_eval': '模型效果评估',
    'feature_iv': '特征IV',
    'overdue_lift': '逾期/Lift',
    'psi_stability': 'PSI稳定',
    'strategy_layer': '策略分层',
  };
  return tagMap[value] || value;
}

function getLabelText(label) {
  const map = { effective: '有效', ineffective: '无效', observing: '观察中' };
  return map[label] || label;
}

// ── 工具函数 ─────────────────────────────────────────────────────────────────
function showLoading(text = '加载中...') {
  let overlay = document.getElementById('loading-overlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.id = 'loading-overlay';
    overlay.className = 'loading-overlay';
    overlay.innerHTML = `
      <div class="spinner"></div>
      <div class="loading-text">${text}</div>
    `;
    document.body.appendChild(overlay);
  } else {
    overlay.querySelector('.loading-text').textContent = text;
    overlay.style.display = 'flex';
  }
}

function hideLoading() {
  const overlay = document.getElementById('loading-overlay');
  if (overlay) {
    overlay.style.display = 'none';
  }
}

function showToast(message, type = 'info') {
  let container = document.getElementById('toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toast-container';
    container.className = 'toast-container';
    document.body.appendChild(container);
  }
  
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  
  setTimeout(() => {
    toast.remove();
  }, 3000);
}

function debounce(fn, delay) {
  let timer = null;
  return function(...args) {
    clearTimeout(timer);
    timer = setTimeout(() => fn.apply(this, args), delay);
  };
}



// ── 渠道组（从上传文件动态加载）───────────────────────────────────────────────
// 注：loadChannelGroups 和 renderChannelGroups 已移除
// 渠道组现在通过 populateChannelColSelect + onChannelColChange 动态从文件加载

// ── 弹窗相关 ─────────────────────────────────────────────────────────────────
function openRecordModal(record = null) {
  const modal = document.getElementById('record-modal');
  modal.classList.remove('hidden');
  
  // 重置表单
  document.getElementById('record-form').reset();
  document.getElementById('record-id').value = '';
  
  // 设置默认日期为今天
  document.getElementById('record-date').value = new Date().toISOString().split('T')[0];
  
  // 如果是编辑模式，填充数据
  if (record) {
    document.getElementById('record-id').value = record.id;
    document.getElementById('record-name').value = record.strategy_name || '';
    document.getElementById('record-date').value = record.adjusted_at || '';
    document.getElementById('record-type').value = record.strategy_type || '模型迭代';
    document.getElementById('record-content').value = record.content || '';
    document.getElementById('record-reason').value = (record.reason_tags || []).join(', ');
    document.getElementById('record-goal').value = record.expected_goal || '';
    document.getElementById('record-notes').value = record.notes || '';
  }
}

function closeRecordModal() {
  document.getElementById('record-modal').classList.add('hidden');
}

function openSaveRecordModal() {
  const modal = document.getElementById('save-record-modal');
  modal.classList.remove('hidden');
  
  // 设置默认日期
  document.getElementById('save-record-date').value = new Date().toISOString().split('T')[0];
  
  // 预填充分析结果到表格
  if (currentState.analysisResult && currentState.analysisResult.result) {
    const result = currentState.analysisResult.result;
    const metrics = result.metrics || {};
    const summary = result.summary || {};
    
    // 填充调整前指标
    const beforeRows = document.querySelectorAll('#before-metrics-body tr');
    beforeRows.forEach(row => {
      const nameInput = row.querySelector('.metric-name');
      const valueInput = row.querySelector('.metric-value');
      const name = nameInput.value;
      
      if (name === 'KS') valueInput.value = (metrics.ks || 0).toFixed(4);
      else if (name === 'AUC') valueInput.value = (metrics.auc || 0).toFixed(4);
      else if (name === 'PSI') valueInput.value = (metrics.psi || 0).toFixed(4);
      else if (name === '逾期率') valueInput.value = ((summary.bad_rate || 0) * 100).toFixed(2) + '%';
    });
  }
}

function closeSaveRecordModal() {
  document.getElementById('save-record-modal').classList.add('hidden');
}

// ── 指标表格操作 ─────────────────────────────────────────────────────────────
function addMetricRow(tbodyId) {
  const tbody = document.getElementById(tbodyId);
  const row = document.createElement('tr');
  row.innerHTML = `
    <td><input type="text" class="form-input metric-name" placeholder="指标名" style="width:100%;"></td>
    <td><input type="text" class="form-input metric-value" placeholder="指标值" style="width:100%;"></td>
    <td><button class="btn btn-sm" onclick="removeMetricRow(this)">×</button></td>
  `;
  tbody.appendChild(row);
}

function removeMetricRow(btn) {
  const row = btn.closest('tr');
  row.remove();
}

function getMetricsFromTable(tbodyId) {
  const metrics = {};
  const tbody = document.getElementById(tbodyId);
  const rows = tbody.querySelectorAll('tr');
  
  rows.forEach(row => {
    const name = row.querySelector('.metric-name').value.trim();
    const value = row.querySelector('.metric-value').value.trim();
    if (name) {
      // 尝试转换数值为数字
      const numValue = parseFloat(value);
      metrics[name] = isNaN(numValue) ? value : numValue;
    }
  });
  
  return metrics;
}

// ── 保存分析结果到记录 ───────────────────────────────────────────────────────
async function submitSaveRecord() {
  const data = {
    strategy_name: document.getElementById('save-record-name').value,
    adjusted_at: document.getElementById('save-record-date').value,
    strategy_type: document.getElementById('save-record-type').value,
    content: '',
    reason_tags: [],
    expected_goal: '',
    notes: '',
    metrics_before: getMetricsFromTable('before-metrics-body'),
    metrics_after: getMetricsFromTable('after-metrics-body'),
    analysis_tags: (currentState.analysisResult && currentState.analysisResult._analysisTags)
      ? currentState.analysisResult._analysisTags.join(',')
      : '',
  };
  
  // 验证必填字段
  if (!data.strategy_name) {
    showToast('请填写策略名称', 'error');
    return;
  }
  if (!data.adjusted_at) {
    showToast('请选择调整日期', 'error');
    return;
  }
  
  try {
    const res = await fetch(`${API_BASE}/records`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
    
    if (res.ok) {
      showToast('保存成功', 'success');
      closeSaveRecordModal();
      loadRecords();
      loadGlobalStats();
    } else {
      const err = await res.json();
      showToast(err.error || '保存失败', 'error');
    }
  } catch (err) {
    showToast('保存失败: ' + err.message, 'error');
  }
}

// ── 新增/编辑记录 ────────────────────────────────────────────────────────────
async function submitRecord() {
  const id = document.getElementById('record-id').value;
  const data = {
    strategy_name: document.getElementById('record-name').value,
    adjusted_at: document.getElementById('record-date').value,
    strategy_type: document.getElementById('record-type').value,
    content: document.getElementById('record-content') ? document.getElementById('record-content').value : '',
    reason_tags: document.getElementById('record-reason') ? document.getElementById('record-reason').value.split(',').map(s => s.trim()).filter(Boolean) : [],
    expected_goal: document.getElementById('record-goal') ? document.getElementById('record-goal').value : '',
    notes: document.getElementById('record-notes') ? document.getElementById('record-notes').value : '',
    metrics_before: {},
    metrics_after: {},
    analysis_tags: (currentState.analysisResult && currentState.analysisResult._analysisTags)
      ? currentState.analysisResult._analysisTags.join(',')
      : '',
  };
  
  // 验证必填字段
  if (!data.strategy_name) {
    showToast('请填写策略名称', 'error');
    return;
  }
  if (!data.adjusted_at) {
    showToast('请选择调整日期', 'error');
    return;
  }
  
  try {
    const url = id ? `${API_BASE}/records/${id}` : `${API_BASE}/records`;
    const method = id ? 'PUT' : 'POST';
    
    const res = await fetch(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
    
    if (res.ok) {
      showToast(id ? '更新成功' : '创建成功', 'success');
      closeRecordModal();
      loadRecords();
      loadGlobalStats();
    } else {
      const err = await res.json();
      showToast(err.error || '操作失败', 'error');
    }
  } catch (err) {
    showToast('操作失败: ' + err.message, 'error');
  }
}

function openReviewModal(recordId) {
  const modal = document.getElementById('review-modal');
  modal.classList.remove('hidden');
  document.getElementById('review-record-id').value = recordId;
  currentState.reviewRecordId = recordId;

  // 重置状态
  document.getElementById('review-file').value = '';
  document.getElementById('review-file-name').style.display = 'none';
  document.getElementById('review-cols-config').classList.add('hidden');
  document.getElementById('review-time-col').innerHTML  = '<option value="">请先上传文件</option>';
  document.getElementById('review-target-col').innerHTML = '<option value="">请先上传文件</option>';
  document.getElementById('review-score-col').innerHTML  = '<option value="">请先上传文件</option>';
  currentState.reviewColumns = [];
  
  // 设置默认调整日期为今天
  const today = new Date().toISOString().split('T')[0];
  document.getElementById('review-adjustment-date').value = today;
  
  // 重置手动输入模式
  document.getElementById('review-analysis-type').value = '';
  document.getElementById('review-conclusion').value = '';
  document.querySelectorAll('input[name="review-effect"]').forEach(r => r.checked = false);
  
  // 重置模式为文件模式
  document.querySelector('input[name="review-mode"][value="file"]').checked = true;
  switchReviewMode('file');
  
  // 重置手动模式下的指标表格
  resetManualMetrics();
}

function closeReviewModal() {
  document.getElementById('review-modal').classList.add('hidden');
}

// ── 复盘模式切换 ─────────────────────────────────────────────────────────────
function switchReviewMode(mode) {
  const fileMode = document.getElementById('review-file-mode');
  const manualMode = document.getElementById('review-manual-mode');
  const btn = document.getElementById('btn-start-review');
  
  if (mode === 'file') {
    fileMode.classList.remove('hidden');
    manualMode.classList.add('hidden');
    btn.textContent = '开始复盘';
  } else {
    fileMode.classList.add('hidden');
    manualMode.classList.remove('hidden');
    btn.textContent = '保存复盘';
  }
}

// ── 手动输入指标表格操作 ─────────────────────────────────────────────────────
function resetManualMetrics() {
  const tbody = document.getElementById('manual-metrics-body');
  tbody.innerHTML = `
    <tr>
      <td><input type="text" class="form-input" value="KS" style="width:100%;" readonly></td>
      <td><input type="text" class="form-input metric-before" placeholder="调整前" style="width:100%;" oninput="calcDelta(this)"></td>
      <td><input type="text" class="form-input metric-after" placeholder="调整后" style="width:100%;" oninput="calcDelta(this)"></td>
      <td><span class="metric-delta" style="font-weight:bold;"></span></td>
      <td><button class="btn btn-sm" onclick="removeManualMetricRow(this)">×</button></td>
    </tr>
    <tr>
      <td><input type="text" class="form-input" value="AUC" style="width:100%;" readonly></td>
      <td><input type="text" class="form-input metric-before" placeholder="调整前" style="width:100%;" oninput="calcDelta(this)"></td>
      <td><input type="text" class="form-input metric-after" placeholder="调整后" style="width:100%;" oninput="calcDelta(this)"></td>
      <td><span class="metric-delta" style="font-weight:bold;"></span></td>
      <td><button class="btn btn-sm" onclick="removeManualMetricRow(this)">×</button></td>
    </tr>
    <tr>
      <td><input type="text" class="form-input" value="PSI" style="width:100%;" readonly></td>
      <td><input type="text" class="form-input metric-before" placeholder="调整前" style="width:100%;" oninput="calcDelta(this)"></td>
      <td><input type="text" class="form-input metric-after" placeholder="调整后" style="width:100%;" oninput="calcDelta(this)"></td>
      <td><span class="metric-delta" style="font-weight:bold;"></span></td>
      <td><button class="btn btn-sm" onclick="removeManualMetricRow(this)">×</button></td>
    </tr>
    <tr>
      <td><input type="text" class="form-input" value="逾期率(%)" style="width:100%;" readonly></td>
      <td><input type="text" class="form-input metric-before" placeholder="调整前" style="width:100%;" oninput="calcDelta(this)"></td>
      <td><input type="text" class="form-input metric-after" placeholder="调整后" style="width:100%;" oninput="calcDelta(this)"></td>
      <td><span class="metric-delta" style="font-weight:bold;"></span></td>
      <td><button class="btn btn-sm" onclick="removeManualMetricRow(this)">×</button></td>
    </tr>
  `;
}

function calcDelta(input) {
  const row = input.closest('tr');
  const beforeVal = parseFloat(row.querySelector('.metric-before').value) || 0;
  const afterVal = parseFloat(row.querySelector('.metric-after').value) || 0;
  const delta = afterVal - beforeVal;
  const deltaSpan = row.querySelector('.metric-delta');
  
  if (isNaN(delta) || (beforeVal === 0 && afterVal === 0)) {
    deltaSpan.textContent = '';
    deltaSpan.style.color = '';
  } else {
    const sign = delta > 0 ? '+' : '';
    deltaSpan.textContent = `${sign}${delta.toFixed(4)}`;
    deltaSpan.style.color = delta > 0 ? '#DC2626' : (delta < 0 ? '#059669' : '');
  }
}

function addManualMetricRow() {
  const tbody = document.getElementById('manual-metrics-body');
  const row = document.createElement('tr');
  row.innerHTML = `
    <td><input type="text" class="form-input" placeholder="指标名" style="width:100%;"></td>
    <td><input type="text" class="form-input metric-before" placeholder="调整前" style="width:100%;" oninput="calcDelta(this)"></td>
    <td><input type="text" class="form-input metric-after" placeholder="调整后" style="width:100%;" oninput="calcDelta(this)"></td>
    <td><span class="metric-delta" style="font-weight:bold;"></span></td>
    <td><button class="btn btn-sm" onclick="removeManualMetricRow(this)">×</button></td>
  `;
  tbody.appendChild(row);
}

function removeManualMetricRow(btn) {
  const tbody = document.getElementById('manual-metrics-body');
  if (tbody.querySelectorAll('tr').length > 1) {
    btn.closest('tr').remove();
  } else {
    showToast('至少保留一行指标', 'warning');
  }
}

// ── 手动创建复盘 ─────────────────────────────────────────────────────────────
async function submitManualReview() {
  const recordId = document.getElementById('review-record-id').value;
  const analysisType = document.getElementById('review-analysis-type').value;
  const conclusion = document.getElementById('review-conclusion').value;
  const effect = document.querySelector('input[name="review-effect"]:checked')?.value || '';
  
  // 获取手动输入的指标
  const comparison = [];
  const tbody = document.getElementById('manual-metrics-body');
  const rows = tbody.querySelectorAll('tr');
  
  rows.forEach(row => {
    const name = row.querySelector('td:first-child input').value.trim();
    const beforeVal = parseFloat(row.querySelector('.metric-before').value) || 0;
    const afterVal = parseFloat(row.querySelector('.metric-after').value) || 0;
    
    if (name) {
      const improved = afterVal > beforeVal; // 默认：值越大越好
      comparison.push({
        label: name,
        before: beforeVal,
        after: afterVal,
        delta: afterVal - beforeVal,
        improved: improved,
        note: ''
      });
    }
  });
  
  if (comparison.length === 0) {
    showToast('请至少填写一个指标', 'error');
    return;
  }
  
  try {
    showLoading('正在保存复盘记录...');
    
    const res = await fetch(`${API_BASE}/reviews/manual`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        record_id: recordId,
        analysis_type: analysisType,
        comparison: comparison,
        ai_conclusion: conclusion,
        manual_label: effect,
        manual_note: conclusion
      })
    });
    
    hideLoading();
    
    if (res.ok) {
      showToast('复盘保存成功', 'success');
      closeReviewModal();
      loadRecords();
      loadReviews();
      loadGlobalStats();
    } else {
      const err = await res.json();
      showToast(err.error || '保存失败', 'error');
    }
  } catch (err) {
    hideLoading();
    showToast('保存失败: ' + err.message, 'error');
  }
}

// ── 复盘文件上传 → 获取列名 ─────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  const reviewFileInput = document.getElementById('review-file');
  if (reviewFileInput) {
    reviewFileInput.addEventListener('change', async (e) => {
      if (!e.target.files.length) return;
      const file = e.target.files[0];

      showLoading('正在读取文件列名...');
      const formData = new FormData();
      formData.append('file', file);

      try {
        const res = await fetch(`${API_BASE}/reviews/file-columns`, {
          method: 'POST',
          body: formData
        });
        const data = await res.json();
        hideLoading();

        if (!res.ok) {
          showToast(data.error || '读取文件失败', 'error');
          return;
        }

        currentState.reviewColumns = data.columns || [];

        // 显示文件名
        const fileNameEl = document.getElementById('review-file-name');
        fileNameEl.textContent = '✅ 已选择：' + file.name;
        fileNameEl.style.display = 'block';

        // 填充列选择器
        const timeSel  = document.getElementById('review-time-col');
        const targetSel = document.getElementById('review-target-col');
        const scoreSel  = document.getElementById('review-score-col');

        const allCols = currentState.reviewColumns;
        const fmt = (arr) => arr.map(c => `<option value="${c}">${c}</option>`).join('');

        timeSel.innerHTML  = fmt(allCols);
        targetSel.innerHTML = fmt(allCols);
        scoreSel.innerHTML  = '<option value="">自动推断（推荐）</option>' + fmt(allCols);

        // 自动选中推荐的列
        if (data.recommended_time)   timeSel.value  = data.recommended_time;
        if (data.recommended_target)  targetSel.value = data.recommended_target;
        if (data.recommended_score)   scoreSel.value  = data.recommended_score;

        // 显示配置区
        document.getElementById('review-cols-config').classList.remove('hidden');
        document.getElementById('btn-start-review').disabled = false;

        showToast('文件读取成功，请选择列', 'success');
      } catch (err) {
        hideLoading();
        showToast('读取文件失败: ' + err.message, 'error');
      }
    });
  }
});

async function submitReview() {
  const mode = document.querySelector('input[name="review-mode"]:checked').value;
  
  if (mode === 'manual') {
    // 手动输入模式
    await submitManualReview();
  } else {
    // 文件分析模式
    await submitFileReview();
  }
}

async function submitFileReview() {
  const recordId  = document.getElementById('review-record-id').value;
  const fileInput = document.getElementById('review-file');
  const timeCol   = document.getElementById('review-time-col').value;
  const adjustmentDate = document.getElementById('review-adjustment-date').value;
  const targetCol = document.getElementById('review-target-col').value;
  const scoreCol  = document.getElementById('review-score-col').value;

  if (!fileInput.files.length) {
    showToast('请上传复盘数据文件', 'error');
    return;
  }
  if (!adjustmentDate) {
    showToast('请选择调整日期（手动输入）', 'error');
    return;
  }
  if (!timeCol) {
    showToast('请选择时间字段（用于判断每条数据的时间）', 'error');
    return;
  }

  const formData = new FormData();
  formData.append('record_id',        recordId);
  formData.append('file',             fileInput.files[0]);
  formData.append('time_col',         timeCol);
  formData.append('adjustment_date',  adjustmentDate);
  formData.append('target_col',       targetCol);
  formData.append('score_col',        scoreCol);

  showLoading('正在调用 AI 复盘 Agent，请稍候...\n（分析时间约10-30秒）');

  try {
    const res = await fetch(`${API_BASE}/reviews`, {
      method: 'POST',
      body: formData
    });
    const data = await res.json();
    hideLoading();

    if (!res.ok) {
      showToast(data.error || '复盘失败', 'error');
      return;
    }

    showToast('AI 复盘完成', 'success');
    closeReviewModal();
    loadRecords();
    loadReviews();
    loadGlobalStats();
    showReviewResult(data);
  } catch (err) {
    hideLoading();
    showToast('复盘失败: ' + err.message, 'error');
  }
}

function showReviewResult(data) {
  const modal = document.getElementById('review-result-modal');
  modal.classList.remove('hidden');
  const body  = document.getElementById('review-result-body');

  // 如果有 HTML 报告 → 嵌入 iframe
  if (data.html_report) {
    body.innerHTML = `
      <iframe srcdoc="${data.html_report.replace(/"/g, '&quot;')}"
        style="width:100%;border:none;min-height:500px;"
        sandbox="allow-scripts allow-same-origin"></iframe>`;
  } else {
    // 回退：简单表格 + AI 结论
    const rows = (data.review_result || []).map(item => `
      <tr>
        <td>${item.label}</td>
        <td>${typeof item.before === 'number' ? item.before.toFixed(4) : item.before}</td>
        <td>${typeof item.after  === 'number' ? item.after.toFixed(4)  : item.after}</td>
        <td style="color:${item.improved ? '#10b981' : '#ef4444'};font-weight:bold;">
          ${item.improved ? '↗' : '↘'} ${Math.abs(item.delta).toFixed(4)}
        </td>
        <td>${item.note || ''}</td>
      </tr>`).join('');

    body.innerHTML = `
      <h4 style="margin-bottom:12px;">指标对比</h4>
      <div class="table-container" style="margin-bottom:20px;">
        <table class="data-table">
          <thead><tr><th>指标</th><th>调整前</th><th>调整后</th><th>变化</th><th>说明</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      <h4 style="margin-bottom:12px;">🤖 AI 复盘结论</h4>
      <div style="background:#eff6ff;border-left:4px solid #2563eb;padding:16px;border-radius:4px;line-height:1.9;white-space:pre-wrap;">${data.ai_conclusion || '-'}</div>
    `;
  }
}

function closeReviewResultModal() {
  document.getElementById('review-result-modal').classList.add('hidden');
}

// ── 人工标注 ─────────────────────────────────────────────────────────────────
function openLabelModal(reviewId, currentLabel, currentNote) {
  const modal = document.getElementById('label-modal');
  modal.classList.remove('hidden');
  document.getElementById('label-review-id').value = reviewId;

  // 重置
  document.querySelectorAll('input[name="manual-label"]').forEach(r => r.checked = false);
  document.getElementById('label-note').value = '';

  if (currentLabel) {
    const radio = document.querySelector(`input[name="manual-label"][value="${currentLabel}"]`);
    if (radio) radio.checked = true;
  }
  if (currentNote) {
    document.getElementById('label-note').value = currentNote;
  }
}

function closeLabelModal() {
  document.getElementById('label-modal').classList.add('hidden');
}

async function submitLabel() {
  const reviewId = document.getElementById('label-review-id').value;
  const label    = document.querySelector('input[name="manual-label"]:checked')?.value || '';
  const note     = document.getElementById('label-note').value;

  if (!label) {
    showToast('请选择标注结论', 'error');
    return;
  }

  try {
    const res = await fetch(`${API_BASE}/reviews/${reviewId}/label`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ manual_label: label, manual_note: note })
    });
    const data = await res.json();
    if (res.ok) {
      showToast('标注保存成功', 'success');
      closeLabelModal();
      loadReviews();
      loadGlobalStats();
    } else {
      showToast(data.error || '保存失败', 'error');
    }
  } catch (err) {
    showToast('保存失败: ' + err.message, 'error');
  }
}

// 全局暴露
window.openLabelModal  = openLabelModal;
window.closeLabelModal = closeLabelModal;
window.submitLabel    = submitLabel;
window.submitSaveRecord = submitSaveRecord;
window.addMetricRow   = addMetricRow;
window.removeMetricRow = removeMetricRow;
window.switchReviewMode = switchReviewMode;
window.addManualMetricRow = addManualMetricRow;
window.removeManualMetricRow = removeManualMetricRow;
window.calcDelta = calcDelta;

// 全局函数暴露
window.viewRecord = (id) => {
  // 查看记录详情
  showToast('查看记录: ' + id, 'info');
};

// 编辑记录
window.openRecordModalForEdit = async (id) => {
  // 先获取最新数据
  try {
    const res = await fetch(`${API_BASE}/records/${id}`);
    const record = await res.json();
    
    // 更新弹窗标题
    document.querySelector('#record-modal .modal-title').textContent = '编辑策略调整记录';
    
    // 打开弹窗并填充数据
    openRecordModal(record);
  } catch (err) {
    showToast('加载记录失败', 'error');
  }
};

window.viewReview = (id) => {
  // 查看复盘详情
  showToast('查看复盘: ' + id, 'info');
};

window.openReviewModal = openReviewModal;
window.toggleAllFeatureCols = toggleAllFeatureCols;
window.updateFeatureSelectedCount = updateFeatureSelectedCount;
window.onChannelColChange = onChannelColChange;
window.toggleAllChannelValues = toggleAllChannelValues;

// 模型分箱分析函数
window.switchBinningTab = switchBinningTab;
window.showBinningModelDetail = showBinningModelDetail;

// 模型相关性分析函数
window.switchCorrTab = switchCorrTab;

// 专家分析分箱详情
window.showExpertBinningModelDetail = showExpertBinningModelDetail;

// ── 知识库问答页面 ──────────────────────────────────────────────────────────
let knowledgeTopicsLoaded = false;

function initKnowledgePage() {
  const inputEl = document.getElementById('knowledge-input');
  const sendBtn = document.getElementById('btn-ask-knowledge');
  const clearBtn = document.getElementById('btn-clear-chat');

  if (!inputEl || !sendBtn) return;

  // 字数统计
  inputEl.addEventListener('input', () => {
    const count = inputEl.value.length;
    const countEl = document.getElementById('knowledge-char-count');
    if (countEl) countEl.textContent = `${count} / 2000`;
  });

  // Ctrl+Enter 发送
  inputEl.addEventListener('keydown', (e) => {
    if (e.ctrlKey && e.key === 'Enter') {
      e.preventDefault();
      askKnowledge();
    }
  });

  sendBtn.addEventListener('click', askKnowledge);

  clearBtn && clearBtn.addEventListener('click', () => {
    const history = document.getElementById('chat-history');
    history.innerHTML = `
      <div class="chat-welcome">
        <div class="chat-welcome-icon">🤖</div>
        <div class="chat-welcome-title">你好！我是 RiskPilot 风控知识助手</div>
        <div class="chat-welcome-desc">
          你可以问我任何风控相关的问题，例如：<br>
          「KS值怎么计算？」「PSI超过0.25怎么办？」「复贷策略如何设计？」
        </div>
      </div>
    `;
  });

  // 自定义标签输入
  const customTagInput = document.getElementById('custom-tag-input');
  if (customTagInput) {
    customTagInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ',') {
        e.preventDefault();
        addCustomTag(customTagInput.value.trim());
        customTagInput.value = '';
      }
    });
  }
}

function addCustomTag(text) {
  if (!text || text.length < 1) return;
  const area = document.getElementById('custom-tags-area');
  if (!area) return;

  // 防重复
  const existing = area.querySelectorAll('.custom-tag-cb');
  for (const cb of existing) {
    if (cb.value === text) return;
  }

  const uid = 'ctag_' + Date.now();
  const label = document.createElement('label');
  label.className = 'tag-checkbox custom-tag';
  label.innerHTML = `
    <input type="checkbox" class="custom-tag-cb" value="${text}" checked>
    <span>${text} <span style="opacity:0.6;cursor:pointer;" onclick="this.closest('label').remove()" title="删除">×</span></span>
  `;
  area.appendChild(label);
}

async function loadKnowledgeTopics() {
  if (knowledgeTopicsLoaded) return;
  try {
    const res = await fetch(`${API_BASE}/knowledge/topics`);
    const data = await res.json();
    renderKnowledgeTopics(data.topics || []);
    knowledgeTopicsLoaded = true;
  } catch (err) {
    console.warn('加载知识主题失败', err);
  }
}

function renderKnowledgeTopics(topics) {
  const container = document.getElementById('knowledge-topics-container');
  if (!container) return;

  container.innerHTML = '';
  topics.forEach(group => {
    const div = document.createElement('div');
    div.className = 'topic-group';
    div.innerHTML = `
      <div class="topic-group-header">
        <span>${group.icon}</span>
        <span>${group.category}</span>
      </div>
    `;
    (group.questions || []).forEach(q => {
      const btn = document.createElement('button');
      btn.className = 'topic-question-btn';
      btn.textContent = q;
      btn.addEventListener('click', () => {
        const inputEl = document.getElementById('knowledge-input');
        if (inputEl) {
          inputEl.value = q;
          inputEl.dispatchEvent(new Event('input'));
          inputEl.focus();
        }
      });
      div.appendChild(btn);
    });
    container.appendChild(div);
  });
}

async function askKnowledge() {
  const inputEl = document.getElementById('knowledge-input');
  const question = (inputEl.value || '').trim();
  if (!question) {
    showToast('请输入问题', 'error');
    return;
  }
  if (question.length > 2000) {
    showToast('问题不能超过2000字', 'error');
    return;
  }

  // 追加用户消息到对话框
  appendChatMessage('user', question);
  inputEl.value = '';
  document.getElementById('knowledge-char-count').textContent = '0 / 2000';

  // 打字指示器
  const typingId = appendTypingIndicator();

  const sendBtn = document.getElementById('btn-ask-knowledge');
  sendBtn.disabled = true;

  try {
    const res = await fetch(`${API_BASE}/knowledge/ask`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question })
    });
    const data = await res.json();

    removeTypingIndicator(typingId);
    sendBtn.disabled = false;

    if (data.answer) {
      appendChatMessage('ai', data.answer, data.model, !data.success);
    } else {
      appendChatMessage('ai', '抱歉，未能获取到回答，请稍后再试。', '', true);
    }
  } catch (err) {
    removeTypingIndicator(typingId);
    sendBtn.disabled = false;
    appendChatMessage('ai', `请求失败：${err.message}`, '', true);
  }
}

function appendChatMessage(role, content, model = '', isError = false) {
  const history = document.getElementById('chat-history');
  // 移除欢迎卡片
  const welcome = history.querySelector('.chat-welcome');
  if (welcome) welcome.remove();

  const div = document.createElement('div');
  div.className = `chat-message ${role}`;

  const avatarIcon = role === 'user' ? '👤' : '🤖';
  const bubbleClass = isError ? 'chat-bubble error' : 'chat-bubble';
  const modelTag = (role === 'ai' && model) ? `<div class="chat-model-tag">by ${model}</div>` : '';

  div.innerHTML = `
    <div class="chat-avatar">${avatarIcon}</div>
    <div>
      <div class="${bubbleClass}">${escapeHtml(content)}</div>
      ${modelTag}
    </div>
  `;
  history.appendChild(div);
  history.scrollTop = history.scrollHeight;
}

function appendTypingIndicator() {
  const history = document.getElementById('chat-history');
  const id = 'typing_' + Date.now();
  const div = document.createElement('div');
  div.className = 'chat-message ai';
  div.id = id;
  div.innerHTML = `
    <div class="chat-avatar">🤖</div>
    <div class="chat-bubble">
      <div class="typing-indicator">
        <div class="typing-dot"></div>
        <div class="typing-dot"></div>
        <div class="typing-dot"></div>
      </div>
    </div>
  `;
  history.appendChild(div);
  history.scrollTop = history.scrollHeight;
  return id;
}

function removeTypingIndicator(id) {
  const el = document.getElementById(id);
  if (el) el.remove();
}

function escapeHtml(text) {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;')
    .replace(/\n/g, '<br>');
}
