"""
策略分析 API 路由
POST /api/analysis/upload     上传文件
POST /api/analysis/run        执行分析（支持新旧两种模式）
POST /api/analysis/expert-analysis  专家深度分析
POST /api/analysis/model-binning     模型分箱分析（新增）
POST /api/analysis/model-correlation 模型相关性分析（新增）
GET  /api/analysis/history    历史列表
GET  /api/analysis/<id>       详情
GET  /api/analysis/<id>/export 导出
GET  /api/analysis/report/<task_id>  下载分析报告
"""

import os
import json
import uuid
import base64
import pandas as pd
from flask import Blueprint, request, jsonify, current_app, send_file
from datetime import datetime
import io

from models.database import db
from models.analysis_task import AnalysisTask
from services.analysis_service import run_analysis
from services.suggestion_service import generate_suggestion, generate_llm_dynamic_suggestion
from services.llm_service import (
    generate_llm_suggestion, check_llm_config, agent_analysis, multi_expert_analysis
)
from services.export_service import export_analysis_report
from services.model_binning_service import (
    run_model_binning_analysis, generate_binning_html_report
)
from services.model_correlation_service import (
    run_correlation_analysis, generate_correlation_html_report
)
from services.rules_analysis_service import (
    run_rule_analysis, generate_rule_html_report
)

analysis_bp = Blueprint('analysis', __name__)

ALLOWED_EXTENSIONS = {'csv', 'xlsx', 'xls'}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# ── 检查大模型配置 ────────────────────────────────────────────────────────────
@analysis_bp.route('/llm-config', methods=['GET'])
def llm_config():
    """获取大模型配置状态"""
    return jsonify(check_llm_config())


# ── 获取文件字段的唯一值（用于渠道组筛选）─────────────────────────────────────
@analysis_bp.route('/column-values', methods=['POST'])
def get_column_values():
    """
    获取指定列的唯一值列表（用于渠道组筛选）
    Body: file_id, column_name
    """
    data = request.get_json()
    file_id = data.get('file_id')
    col_name = data.get('column_name', '')
    
    if not file_id:
        return jsonify({'error': '缺少 file_id'}), 400
    if not col_name:
        return jsonify({'error': '缺少 column_name'}), 400
    
    file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], file_id)
    if not os.path.exists(file_path):
        return jsonify({'error': '文件不存在，请重新上传'}), 400
    
    ext = file_id.rsplit('.', 1)[-1].lower() if '.' in file_id else 'csv'
    
    try:
        if ext == 'csv':
            df = pd.read_csv(file_path)
        else:
            df = pd.read_excel(file_path)
        
        if col_name not in df.columns:
            return jsonify({'error': f'列 "{col_name}" 不存在'}), 400
        
        # 获取唯一值（最多返回100个）
        unique_values = df[col_name].dropna().astype(str).unique().tolist()[:100]
        return jsonify({
            'column_name': col_name,
            'unique_values': sorted(unique_values),
            'total_count': len(unique_values),
        })
    except Exception as e:
        return jsonify({'error': f'读取文件失败：{str(e)}'}), 400


# ── 上传文件 ─────────────────────────────────────────────────────────────────
@analysis_bp.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': '未选择文件'}), 400

    f = request.files['file']
    if not f.filename or not allowed_file(f.filename):
        return jsonify({'error': '文件类型不支持，仅支持 CSV/Excel'}), 400

    ext      = f.filename.rsplit('.', 1)[1].lower()
    new_name = f"{uuid.uuid4().hex}.{ext}"
    save_path = os.path.join(current_app.config['UPLOAD_FOLDER'], new_name)
    f.save(save_path)

    # 预读取列信息
    try:
        if ext == 'csv':
            df = pd.read_csv(save_path, nrows=5)
        else:
            df = pd.read_excel(save_path, nrows=5)
        columns = list(df.columns)
        # 统计行数
        if ext == 'csv':
            full_df = pd.read_csv(save_path)
        else:
            full_df = pd.read_excel(save_path)
        n_rows = len(full_df)
    except Exception as e:
        return jsonify({'error': f'文件解析失败：{str(e)}'}), 400

    return jsonify({
        'file_id':   new_name,
        'file_name': f.filename,
        'file_type': ext,
        'n_rows':    n_rows,
        'n_cols':    len(columns),
        'columns':   columns,
    })


# ── 多模型综合分析（已移除固定模板，使用专家分析）────────────────────────────
@analysis_bp.route('/multi-model', methods=['POST'])
def multi_model_analysis():
    """
    多模型综合分析
    现在使用多专家深度分析替代原来的固定模板报告

    Body:
        file_id, file_name, file_type,
        target_col, feature_cols (list), n_bins, user_note,
        analysis_tags, use_agent, channel_col, channel_values
    """
    # 直接调用 expert_analysis 的逻辑
    return expert_analysis()


# ── 多专家深度分析（新接口）───────────────────────────────────────────────────
@analysis_bp.route('/expert-analysis', methods=['POST'])
def expert_analysis():
    """
    多专家深度分析接口
    - 数据分析师、金融建模师、风控策略专家真正调用skill进行分析
    - 不再使用固定格式报告
    - 支持分析结果下载
    - 支持业务场景参数（首复贷、国家、模块）

    Body:
        file_id, file_name, file_type,
        target_col, feature_cols (list), n_bins, user_note,
        analysis_tags, channel_col, channel_values,
        biz_scenario, biz_country, biz_module
    """
    data = request.get_json()
    file_id       = data.get('file_id')
    file_name     = data.get('file_name', '')
    file_type     = data.get('file_type', 'csv')
    target_col    = data.get('target_col', '')
    feature_cols  = data.get('feature_cols', [])
    n_bins        = int(data.get('n_bins', 10))
    user_note     = data.get('user_note', '')
    analysis_tags = data.get('analysis_tags', [])
    channel_col   = data.get('channel_col', '')
    channel_values = data.get('channel_values', [])
    # 业务场景参数
    biz_scenario  = data.get('biz_scenario', '')  # first_loan / repeat_loan
    biz_country   = data.get('biz_country', '')   # india / indonesia / philippines
    biz_module    = data.get('biz_module', '')    # model / rule

    if not file_id or not target_col:
        return jsonify({'error': '缺少必要参数（file_id / target_col）'}), 400

    file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], file_id)
    if not os.path.exists(file_path):
        return jsonify({'error': '文件不存在，请重新上传'}), 400

    try:
        # 读取数据
        if file_type == 'csv':
            df = pd.read_csv(file_path)
        else:
            df = pd.read_excel(file_path)

        # 渠道筛选
        if channel_col and channel_values and channel_col in df.columns:
            original_count = len(df)
            df = df[df[channel_col].astype(str).isin(channel_values)]
            filtered_note = f' [渠道筛选: {channel_col}={channel_values}, 过滤 {original_count - len(df)} 条]'
            user_note = (user_note or '') + filtered_note

        # 过滤有效列
        valid_cols = [c for c in feature_cols if c in df.columns] if feature_cols else []
        if not valid_cols:
            # 尝试自动识别
            exclude = {target_col, 'label', 'overdue', 'y', 'flag', 'id', 'customer_id'}
            valid_cols = [c for c in df.columns 
                         if c.lower() not in exclude 
                         and pd.api.types.is_numeric_dtype(df[c])
                         and df[c].nunique() > 5][:20]

        if not valid_cols:
            return jsonify({'error': '未找到有效的分析特征列'}), 400

        # 获取上传文件夹路径
        upload_folder = current_app.config['UPLOAD_FOLDER']

        # 执行多专家深度分析
        multi_result = multi_expert_analysis(
            metrics={},  # 会从skill分析中获取
            bins=[],
            file_info={
                'file_name': file_name,
                'n_rows': len(df),
                'target_col': target_col,
                'score_col': valid_cols[0] if valid_cols else ''
            },
            user_note=user_note,
            analysis_tags=analysis_tags,
            file_path=file_path,
            target_col=target_col,
            score_cols=valid_cols,
            file_type=file_type,
            n_bins=n_bins,
            upload_folder=upload_folder,
            # 业务场景参数
            biz_scenario=biz_scenario,
            biz_country=biz_country,
            biz_module=biz_module,
        )

        # 保存到数据库
        task = AnalysisTask(
            file_name     = file_name,
            file_type     = file_type,
            analysis_type = 'expert_analysis',
            analysis_tags = ','.join(analysis_tags) if isinstance(analysis_tags, list) else analysis_tags,
            feature_cols  = ','.join(valid_cols),
            target_col    = target_col,
            score_col     = valid_cols[0] if valid_cols else '',
            n_bins        = n_bins,
            user_note     = user_note,
            result_json   = json.dumps({
                'expert_analysis': True,
                'data_summary': multi_result.get('data_summary', ''),
                'model_summary': multi_result.get('model_summary', ''),
            }, ensure_ascii=False),
            suggestion    = json.dumps({
                'type': 'expert_analysis',
                'expert_reports': {
                    k: {
                        'diagnosis': v.get('diagnosis', ''), 
                        'evaluation': v.get('evaluation', ''),
                        'strategy_advice': v.get('strategy_advice', ''),
                        # === 新增：结构化数据 ===
                        'metrics': v.get('metrics', {}),
                        'bins': v.get('bins', []),
                        'feature_importance': v.get('feature_importance', []),
                        'model_performance': v.get('model_performance', []),
                        'correlation_matrix': v.get('correlation_matrix', {}),
                        'bin_results': v.get('bin_results', []),
                    }
                    for k, v in multi_result.get('expert_reports', {}).items()
                },
                'final_report': multi_result.get('final_report', ''),
                'report_path': multi_result.get('report_path', ''),
                'suggestions': multi_result.get('suggestions', []),
            }, ensure_ascii=False),
        )
        db.session.add(task)
        db.session.commit()

        return jsonify({
            'task_id': task.id,
            'mode': 'expert_analysis',
            'success': multi_result.get('success', False),
            'expert_reports': multi_result.get('expert_reports', {}),
            'final_report': multi_result.get('final_report', ''),
            'report_path': multi_result.get('report_path', ''),
            'report_filename': multi_result.get('report_filename', ''),
            'data_summary': multi_result.get('data_summary', ''),
            'model_summary': multi_result.get('model_summary', ''),
            'suggestions': multi_result.get('suggestions', []),
            'n_samples': len(df),
            'n_features': len(valid_cols),
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'专家分析失败：{str(e)}'}), 500


# ── 模型分箱分析 ───────────────────────────────────────────────────────────────
@analysis_bp.route('/model-binning', methods=['POST'])
def model_binning_analysis():
    """
    模型分箱分析接口
    使用 model_binning_service.py 的计算口径
    
    Body:
        file_id, file_name, file_type,
        target_col, feature_cols (list), n_bins, channel_col, channel_values,
        biz_scenario, biz_country, biz_module
    """
    data = request.get_json()
    file_id        = data.get('file_id')
    file_name      = data.get('file_name', '')
    file_type      = data.get('file_type', 'csv')
    target_col     = data.get('target_col', '')
    feature_cols   = data.get('feature_cols', [])
    n_bins         = int(data.get('n_bins', 10))
    channel_col    = data.get('channel_col', '')
    channel_values = data.get('channel_values', [])
    biz_scenario   = data.get('biz_scenario', '')
    biz_country    = data.get('biz_country', '')
    biz_module     = data.get('biz_module', 'model')  # 默认模型模式
    threshold      = float(data.get('threshold', 1.0))

    if not file_id or not target_col:
        return jsonify({'error': '缺少必要参数（file_id / target_col）'}), 400

    file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], file_id)
    if not os.path.exists(file_path):
        return jsonify({'error': '文件不存在，请重新上传'}), 400

    try:
        # 读取数据
        if file_type == 'csv':
            df = pd.read_csv(file_path)
        else:
            df = pd.read_excel(file_path)

        # 渠道筛选
        if channel_col and channel_values and channel_col in df.columns:
            original_count = len(df)
            df = df[df[channel_col].astype(str).isin(channel_values)]
            filtered_note = f' [渠道筛选: {channel_col}={channel_values}, 过滤 {original_count - len(df)} 条]'

        # 获取分析日期
        analysis_date = datetime.now().strftime('%Y-%m-%d %H:%M')

        # 执行模型分箱分析
        binning_result = run_model_binning_analysis(
            df=df,
            label_col=target_col,
            n_bins=n_bins,
            threshold=threshold
        )

        if 'error' in binning_result:
            return jsonify({'error': binning_result['error']}), 400

        # 生成HTML报告
        html_report = generate_binning_html_report(binning_result, analysis_date)

        # 保存报告到文件
        upload_folder = current_app.config['UPLOAD_FOLDER']
        report_filename = f"model_binning_{file_id.replace('.', '_')}_{datetime.now().strftime('%Y%m%d%H%M%S')}.html"
        report_path = os.path.join(upload_folder, 'reports', report_filename)
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(html_report)

        # 保存到数据库
        task = AnalysisTask(
            file_name     = file_name,
            file_type     = file_type,
            analysis_type = 'model_binning',
            analysis_tags = biz_module,
            feature_cols  = ','.join(feature_cols) if isinstance(feature_cols, list) else '',
            target_col    = target_col,
            score_col     = feature_cols[0] if isinstance(feature_cols, list) and feature_cols else '',
            n_bins        = n_bins,
            user_note     = f"[模型分箱分析] 分箱数={n_bins} | {filtered_note}" if channel_col else f"[模型分箱分析] 分箱数={n_bins}",
            result_json   = json.dumps({
                'analysis_type': 'model_binning',
                'data_summary': binning_result.get('data_summary', {}),
                'model_count': len(binning_result.get('all_results', [])),
            }, ensure_ascii=False),
            suggestion    = json.dumps({
                'type': 'model_binning',
                'report_path': report_path,
                'charts': binning_result.get('charts', {}),
                'model_summary_table': binning_result.get('model_summary_table', ''),
                'binning_details': binning_result.get('binning_details', {}),
            }, ensure_ascii=False),
        )
        db.session.add(task)
        db.session.commit()

        # 生成 LLM 动态策略建议
        llm_suggestion = generate_llm_dynamic_suggestion(
            analysis_data={
                'model_summary': binning_result.get('summary_df', []),
                'data_summary': binning_result.get('data_summary', {}),
                'all_results': binning_result.get('all_results', []),
            },
            biz_scenario=biz_scenario,
            biz_country=biz_country,
            biz_module=biz_module,
        )
        ai_suggestions = llm_suggestion.get('suggestions', [])

        return jsonify({
            'task_id': task.id,
            'mode': 'model_binning',
            'success': True,
            'data_summary': binning_result.get('data_summary', {}),
            'model_summary': binning_result.get('summary_df', []),
            'all_results': binning_result.get('all_results', []),
            'charts': binning_result.get('charts', {}),
            'model_summary_table': binning_result.get('model_summary_table', ''),
            'binning_details': binning_result.get('binning_details', {}),
            'report_path': report_path,
            'report_filename': report_filename,
            'html_report': html_report,
            'report_html': html_report,  # 前端兼容字段
            'ai_suggestion': ai_suggestions,  # LLM 动态生成的策略建议
            'ai_suggestion_source': llm_suggestion.get('source', 'fallback'),
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'模型分箱分析失败：{str(e)}'}), 500


# ── 模型相关性分析 ────────────────────────────────────────────────────────────
@analysis_bp.route('/model-correlation', methods=['POST'])
def model_correlation_analysis():
    """
    模型相关性分析接口
    使用 model_correlation_service.py 的计算口径
    
    Body:
        file_id, file_name, file_type,
        target_col, feature_cols (list), channel_col, channel_values,
        biz_scenario, biz_country, biz_module
    """
    data = request.get_json()
    file_id        = data.get('file_id')
    file_name      = data.get('file_name', '')
    file_type      = data.get('file_type', 'csv')
    target_col     = data.get('target_col', '')
    feature_cols   = data.get('feature_cols', [])
    channel_col    = data.get('channel_col', '')
    channel_values = data.get('channel_values', [])
    biz_scenario   = data.get('biz_scenario', '')
    biz_country    = data.get('biz_country', '')
    biz_module     = data.get('biz_module', 'model')  # 默认模型模式
    missing_thresh = float(data.get('missing_thresh', 0.3))

    if not file_id or not target_col:
        return jsonify({'error': '缺少必要参数（file_id / target_col）'}), 400

    file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], file_id)
    if not os.path.exists(file_path):
        return jsonify({'error': '文件不存在，请重新上传'}), 400

    try:
        # 读取数据
        if file_type == 'csv':
            df = pd.read_csv(file_path)
        else:
            df = pd.read_excel(file_path)

        # 渠道筛选
        if channel_col and channel_values and channel_col in df.columns:
            original_count = len(df)
            df = df[df[channel_col].astype(str).isin(channel_values)]
            filtered_note = f' [渠道筛选: {channel_col}={channel_values}, 过滤 {original_count - len(df)} 条]'

        # 获取分析日期
        analysis_date = datetime.now().strftime('%Y-%m-%d %H:%M')

        # 执行模型相关性分析
        corr_result = run_correlation_analysis(
            df=df,
            target_col=target_col,
            score_cols=feature_cols if isinstance(feature_cols, list) and feature_cols else None,
            missing_thresh=missing_thresh
        )

        if 'error' in corr_result:
            return jsonify({'error': corr_result['error']}), 400

        # 生成HTML报告
        html_report = generate_correlation_html_report(corr_result, analysis_date)

        # 保存报告到文件
        upload_folder = current_app.config['UPLOAD_FOLDER']
        report_filename = f"model_correlation_{file_id.replace('.', '_')}_{datetime.now().strftime('%Y%m%d%H%M%S')}.html"
        report_path = os.path.join(upload_folder, 'reports', report_filename)
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(html_report)

        # 保存到数据库
        task = AnalysisTask(
            file_name     = file_name,
            file_type     = file_type,
            analysis_type = 'model_correlation',
            analysis_tags = biz_module,
            feature_cols  = ','.join(feature_cols) if isinstance(feature_cols, list) else '',
            target_col    = target_col,
            score_col     = feature_cols[0] if isinstance(feature_cols, list) and feature_cols else '',
            n_bins        = 10,
            user_note     = f"[模型相关性分析] | {filtered_note}" if channel_col else "[模型相关性分析]",
            result_json   = json.dumps({
                'analysis_type': 'model_correlation',
                'data_summary': corr_result.get('data_summary', {}),
                'model_count': len(corr_result.get('score_cols', [])),
            }, ensure_ascii=False),
            suggestion    = json.dumps({
                'type': 'model_correlation',
                'report_path': report_path,
                'charts': corr_result.get('charts', {}),
                'perf_table_html': corr_result.get('perf_table_html', ''),
                'strategy_table_html': corr_result.get('strategy_table_html', ''),
            }, ensure_ascii=False),
        )
        db.session.add(task)
        db.session.commit()

        # 生成 LLM 动态策略建议
        llm_suggestion = generate_llm_dynamic_suggestion(
            analysis_data={
                'performance': corr_result.get('performance', []),
                'correlation': corr_result.get('correlation', []),
                'complementarity': corr_result.get('complementarity', []),
                'strategy_metrics': corr_result.get('strategy_metrics', {}),
                'data_summary': corr_result.get('data_summary', {}),
            },
            biz_scenario=biz_scenario,
            biz_country=biz_country,
            biz_module=biz_module,
        )
        ai_suggestions = llm_suggestion.get('suggestions', [])

        return jsonify({
            'task_id': task.id,
            'mode': 'model_correlation',
            'success': True,
            'data_summary': corr_result.get('data_summary', {}),
            'performance': corr_result.get('performance', []),
            'correlation': corr_result.get('correlation', []),
            'complementarity': corr_result.get('complementarity', []),
            'strategy_metrics': corr_result.get('strategy_metrics', {}),
            'charts': corr_result.get('charts', {}),
            'perf_table_html': corr_result.get('perf_table_html', ''),
            'strategy_table_html': corr_result.get('strategy_table_html', ''),
            'report_path': report_path,
            'report_filename': report_filename,
            'html_report': html_report,
            'report_html': html_report,  # 前端兼容字段
            'ai_suggestion': ai_suggestions,  # LLM 动态生成的策略建议
            'ai_suggestion_source': llm_suggestion.get('source', 'fallback'),
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'模型相关性分析失败：{str(e)}'}), 500


# ── 下载分析报告 ──────────────────────────────────────────────────────────────
@analysis_bp.route('/report/<task_id>', methods=['GET'])
def download_report(task_id):
    """
    下载分析报告（Markdown格式）
    """
    task = AnalysisTask.query.get_or_404(task_id)
    
    # 优先从文件下载
    suggestion = json.loads(task.suggestion or '{}')
    report_path = suggestion.get('report_path', '')
    
    if report_path and os.path.exists(report_path):
        return send_file(
            report_path,
            as_attachment=True,
            download_name=f"分析报告_{task.file_name}_{task.id}.md",
            mimetype='text/markdown'
        )
    
    # 如果文件不存在，从数据库内容生成
    final_report = suggestion.get('final_report', '')
    if not final_report:
        final_report = f"""# 分析报告

文件名：{task.file_name}
分析类型：{task.analysis_type}
分析时间：{task.created_at}

（报告内容为空或已过期）
"""
    
    # 生成临时文件
    buffer = io.BytesIO()
    buffer.write(final_report.encode('utf-8'))
    buffer.seek(0)
    
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"分析报告_{task.file_name}_{task.id}.md",
        mimetype='text/markdown'
    )


# ── 执行分析 ─────────────────────────────────────────────────────────────────
@analysis_bp.route('/run', methods=['POST'])
def run():
    """
    执行分析（双模式）

    模式一（expert_analysis=true）：
      - 调用多专家深度分析（数据分析师+建模师+策略专家）
      - 真正调用skill进行专业分析

    模式二（默认）：
      - 使用传统分析 + 多专家建议
    """
    data = request.get_json()
    file_id       = data.get('file_id')
    file_name     = data.get('file_name', '')
    file_type     = data.get('file_type', 'csv')
    analysis_type = data.get('analysis_type', 'model_eval')
    target_col    = data.get('target_col', '')
    score_col     = data.get('score_col', '')
    n_bins        = int(data.get('n_bins', 10))
    user_note     = data.get('user_note', '')
    # ── 新增字段 ────────────────────────────────────────────────────────────
    analysis_tags = data.get('analysis_tags', [])   # 分析类型标签列表
    feature_cols  = data.get('feature_cols', [])    # 分析特征列列表
    # ── Agent 分析参数 ────────────────────────────────────────────────────
    agent_type    = data.get('agent_type', '')       # 空=传统模式，data/model/strategy/all=Agent模式
    use_agent     = data.get('use_agent', False)     # 显式开关
    # ── 渠道组筛选 ──────────────────────────────────────────────────────────
    channel_col   = data.get('channel_col', '')     # 渠道列名
    channel_values = data.get('channel_values', []) # 选择的渠道值列表（空=不过滤）
    # ── 业务场景参数 ─────────────────────────────────────────────────────────
    biz_scenario  = data.get('biz_scenario', '')  # first_loan / repeat_loan
    biz_country   = data.get('biz_country', '')   # india / indonesia / philippines
    biz_module    = data.get('biz_module', '')    # model / rule

    if not file_id or not target_col:
        return jsonify({'error': '缺少必要参数'}), 400

    file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], file_id)
    if not os.path.exists(file_path):
        return jsonify({'error': '文件不存在，请重新上传'}), 400

    try:
        if file_type == 'csv':
            df = pd.read_csv(file_path)
        else:
            df = pd.read_excel(file_path)
    except Exception as e:
        return jsonify({'error': f'读取文件失败：{str(e)}'}), 400

    if target_col not in df.columns:
        return jsonify({'error': f'目标列 "{target_col}" 不存在，请检查列名'}), 400
    if score_col and score_col not in df.columns:
        return jsonify({'error': f'分数列 "{score_col}" 不存在，请检查列名'}), 400

    # ── 渠道组筛选 ──────────────────────────────────────────────────────────
    if channel_col and channel_values and channel_col in df.columns:
        original_count = len(df)
        df = df[df[channel_col].astype(str).isin(channel_values)]
        filtered_count = len(df)
        user_note = (user_note or '') + f' [渠道筛选: {channel_col}={channel_values}, 过滤 {original_count - filtered_count} 条, 剩余 {filtered_count} 条]'

    # ── 模式一：多专家深度分析（优先）─────────────────────────────────────
    # 当选择多个特征列时，自动使用多专家深度分析
    expert_analysis_mode = data.get('expert_analysis', False)
    is_multi_feature = isinstance(feature_cols, list) and len(feature_cols) >= 1
    
    if expert_analysis_mode or is_multi_feature:
        try:
            # 过滤有效列
            valid_cols = [c for c in feature_cols if c in df.columns] if feature_cols else []
            if not valid_cols:
                valid_cols = [score_col] if score_col and score_col in df.columns else []
            
            upload_folder = current_app.config['UPLOAD_FOLDER']
            
            multi_result = multi_expert_analysis(
                metrics={},
                bins=[],
                file_info={
                    'file_name': file_name,
                    'n_rows': len(df),
                    'target_col': target_col,
                    'score_col': valid_cols[0] if valid_cols else ''
                },
                user_note=user_note,
                analysis_tags=analysis_tags,
                file_path=file_path,
                target_col=target_col,
                score_cols=valid_cols,
                file_type=file_type,
                n_bins=n_bins,
                upload_folder=upload_folder,
                # 业务场景参数
                biz_scenario=biz_scenario,
                biz_country=biz_country,
                biz_module=biz_module,
            )
            
            task = AnalysisTask(
                file_name     = file_name,
                file_type     = file_type,
                analysis_type = analysis_type,
                analysis_tags = ','.join(analysis_tags) if isinstance(analysis_tags, list) else analysis_tags,
                feature_cols  = ','.join(valid_cols),
                target_col    = target_col,
                score_col     = valid_cols[0] if valid_cols else '',
                n_bins        = n_bins,
                user_note     = user_note,
                result_json   = json.dumps({
                    'expert_analysis': True,
                    'data_summary': multi_result.get('data_summary', ''),
                    'model_summary': multi_result.get('model_summary', ''),
                }, ensure_ascii=False),
                suggestion    = json.dumps({
                    'type': 'expert_analysis',
                    'expert_reports': {
                        k: {'diagnosis': v.get('diagnosis', ''), 
                            'evaluation': v.get('evaluation', ''),
                            'strategy_advice': v.get('strategy_advice', '')}
                        for k, v in multi_result.get('expert_reports', {}).items()
                    },
                    'final_report': multi_result.get('final_report', ''),
                    'report_path': multi_result.get('report_path', ''),
                    'suggestions': multi_result.get('suggestions', []),
                }, ensure_ascii=False),
            )
            db.session.add(task)
            db.session.commit()

            return jsonify({
                'task_id': task.id,
                'mode': 'expert_analysis',
                'success': multi_result.get('success', False),
                'expert_reports': multi_result.get('expert_reports', {}),
                'final_report': multi_result.get('final_report', ''),
                'report_path': multi_result.get('report_path', ''),
                'report_filename': multi_result.get('report_filename', ''),
                'data_summary': multi_result.get('data_summary', ''),
                'model_summary': multi_result.get('model_summary', ''),
                'suggestions': multi_result.get('suggestions', []),
                'n_samples': len(df),
            })

        except Exception as e:
            import traceback
            traceback.print_exc()
            return jsonify({'error': f'专家分析失败：{str(e)}'}), 500

    # ── 模式二：传统分析 + 多专家建议 ───────────────────────────────
    try:
        result = run_analysis(df, score_col, target_col, n_bins)
    except Exception as e:
        return jsonify({'error': f'分析执行失败：{str(e)}'}), 500

    file_info = {
        'file_name': file_name,
        'n_rows': len(df),
        'target_col': target_col,
        'score_col': score_col
    }

    combined_metrics = {**result['metrics'], 'bad_rate': result['summary']['bad_rate']}
    
    # 使用多专家协作分析
    multi_expert_result = multi_expert_analysis(
        metrics=combined_metrics,
        bins=result['bins'],
        file_info=file_info,
        user_note=user_note,
        analysis_tags=analysis_tags,
        # 业务场景参数
        biz_scenario=biz_scenario,
        biz_country=biz_country,
        biz_module=biz_module,
    )
    
    suggestions = multi_expert_result['suggestions']
    expert_reports = multi_expert_result.get('expert_reports', {})
    final_report = multi_expert_result.get('final_report', '')

    task = AnalysisTask(
        file_name     = file_name,
        file_type     = file_type,
        analysis_type = analysis_type,
        analysis_tags = ','.join(analysis_tags) if isinstance(analysis_tags, list) else analysis_tags,
        feature_cols  = ','.join(feature_cols) if isinstance(feature_cols, list) else feature_cols,
        target_col    = target_col,
        score_col     = score_col,
        n_bins        = n_bins,
        user_note     = user_note,
        result_json   = json.dumps(result, ensure_ascii=False),
        suggestion    = json.dumps({
            'type': 'multi_expert',
            'expert_reports': expert_reports,
            'final_report': final_report,
            'suggestions': suggestions,
        }, ensure_ascii=False),
    )
    db.session.add(task)
    db.session.commit()

    return jsonify({
        'task_id':        task.id,
        'result':         result,
        'suggestion':     suggestions,
        'expert_reports': expert_reports,
        'final_report':   final_report,
        'mode':           'multi_expert',
    })


# ── 历史列表 ─────────────────────────────────────────────────────────────────
@analysis_bp.route('/history', methods=['GET'])
def history():
    page  = int(request.args.get('page', 1))
    limit = int(request.args.get('limit', 20))
    tasks = AnalysisTask.query.order_by(AnalysisTask.created_at.desc()) \
                              .offset((page - 1) * limit).limit(limit).all()
    total = AnalysisTask.query.count()

    result_list = []
    for t in tasks:
        d = t.to_dict()
        # 简化 result，只保留 metrics 和 summary
        if d.get('result'):
            d['result'] = {
                'summary': d['result'].get('summary', {}),
                'metrics': d['result'].get('metrics', {}),
            }
        result_list.append(d)

    return jsonify({'tasks': result_list, 'total': total})


# ── 详情 ─────────────────────────────────────────────────────────────────────
@analysis_bp.route('/<int:task_id>', methods=['GET'])
def detail(task_id):
    task = AnalysisTask.query.get_or_404(task_id)
    return jsonify(task.to_dict())


# ── 导出 ─────────────────────────────────────────────────────────────────────
@analysis_bp.route('/<int:task_id>/export', methods=['GET'])
def export(task_id):
    task = AnalysisTask.query.get_or_404(task_id)
    excel_bytes = export_analysis_report(task.to_dict())
    return send_file(
        io.BytesIO(excel_bytes),
        as_attachment=True,
        download_name=f"分析报告_{task.file_name}_{task.id}.xlsx",
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


# ════════════════════════════════════════════════════════════════════════════════
# 规则分析 API
# ════════════════════════════════════════════════════════════════════════════════

@analysis_bp.route('/rule-analysis', methods=['POST'])
def rule_analysis():
    """
    规则分析接口
    
    Body:
        file_id, file_name, file_type,
        target_col, rule_cols (list), score_col (optional),
        biz_scenario, biz_country, biz_module
    """
    data = request.get_json()
    file_id        = data.get('file_id')
    file_name      = data.get('file_name', '')
    file_type      = data.get('file_type', 'csv')
    target_col     = data.get('target_col', '')
    rule_cols      = data.get('rule_cols', [])
    score_col      = data.get('score_col', '')
    channel_col    = data.get('channel_col', '')
    channel_values = data.get('channel_values', [])
    biz_scenario   = data.get('biz_scenario', '')
    biz_country    = data.get('biz_country', '')
    biz_module     = data.get('biz_module', 'rule')

    if not file_id or not target_col:
        return jsonify({'error': '缺少必要参数（file_id / target_col）'}), 400
    
    if not rule_cols:
        return jsonify({'error': '缺少规则列（rule_cols）'}), 400

    file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], file_id)
    if not os.path.exists(file_path):
        return jsonify({'error': '文件不存在，请重新上传'}), 400

    try:
        # 读取数据
        if file_type == 'csv':
            df = pd.read_csv(file_path)
        else:
            df = pd.read_excel(file_path)

        # 渠道筛选
        filtered_note = ''
        if channel_col and channel_values and channel_col in df.columns:
            original_count = len(df)
            df = df[df[channel_col].astype(str).isin(channel_values)]
            filtered_count = len(df)
            filtered_note = f' [渠道筛选: {channel_col}={channel_values}, 过滤 {original_count - filtered_count} 条, 剩余 {filtered_count} 条]'

        # 验证规则列存在
        valid_rule_cols = [c for c in rule_cols if c in df.columns]
        if not valid_rule_cols:
            return jsonify({'error': f'未找到有效的规则列'}), 400

        # 验证目标列
        if target_col not in df.columns:
            return jsonify({'error': f'目标列 "{target_col}" 不存在'}), 400

        # 获取分析日期
        analysis_date = datetime.now().strftime('%Y-%m-%d %H:%M')

        # 执行规则分析
        rule_result = run_rule_analysis(
            df=df,
            rule_cols=valid_rule_cols,
            target_col=target_col,
            score_col=score_col if score_col and score_col in df.columns else None,
        )

        # 生成HTML报告
        html_report = generate_rule_html_report(rule_result, analysis_date)

        # 保存报告到文件
        upload_folder = current_app.config['UPLOAD_FOLDER']
        report_filename = f"rule_analysis_{file_id.replace('.', '_')}_{datetime.now().strftime('%Y%m%d%H%M%S')}.html"
        report_path = os.path.join(upload_folder, 'reports', report_filename)
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(html_report)

        # 保存到数据库
        task = AnalysisTask(
            file_name     = file_name,
            file_type     = file_type,
            analysis_type = 'rule_analysis',
            analysis_tags = 'rule',
            feature_cols  = ','.join(valid_rule_cols),
            target_col    = target_col,
            score_col     = score_col,
            n_bins        = 0,
            user_note     = f"[规则分析] 规则数={len(valid_rule_cols)}{filtered_note}",
            result_json   = json.dumps({
                'analysis_type': 'rule_analysis',
                'data_summary': rule_result.get('data_summary', {}),
                'rule_count': len(valid_rule_cols),
            }, ensure_ascii=False),
            suggestion    = json.dumps({
                'type': 'rule_analysis',
                'report_path': report_path,
                'charts': rule_result.get('charts', {}),
                'summary_table': rule_result.get('summary_table', ''),
            }, ensure_ascii=False),
        )
        db.session.add(task)
        db.session.commit()

        # 生成 LLM 动态策略建议（传入规则分析数据）
        llm_suggestion = generate_llm_dynamic_suggestion(
            analysis_data={
                'rule_analysis': True,
                'data_summary': rule_result.get('data_summary', {}),
                'rule_binning': rule_result.get('rule_binning', {}),
                'user_profile': rule_result.get('user_profile', {}),
            },
            biz_scenario=biz_scenario,
            biz_country=biz_country,
            biz_module=biz_module,
        )
        ai_suggestions = llm_suggestion.get('suggestions', [])

        return jsonify({
            'task_id': task.id,
            'mode': 'rule_analysis',
            'success': True,
            'data_summary': rule_result.get('data_summary', {}),
            'rule_binning': rule_result.get('rule_binning', {}),
            'user_profile': rule_result.get('user_profile', {}),
            'charts': rule_result.get('charts', {}),
            'summary_table': rule_result.get('summary_table', ''),
            'report_path': report_path,
            'report_filename': report_filename,
            'html_report': html_report,
            'report_html': html_report,
            'ai_suggestion': ai_suggestions,
            'ai_suggestion_source': llm_suggestion.get('source', 'fallback'),
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'规则分析失败：{str(e)}'}), 500


@analysis_bp.route('/rule-columns', methods=['POST'])
def get_rule_columns():
    """
    获取可用于分析的规则列
    Body: file_id, file_type
    """
    data = request.get_json()
    file_id = data.get('file_id')
    file_type = data.get('file_type', 'csv')
    
    if not file_id:
        return jsonify({'error': '缺少 file_id'}), 400
    
    file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], file_id)
    if not os.path.exists(file_path):
        return jsonify({'error': '文件不存在'}), 400
    
    try:
        if file_type == 'csv':
            df = pd.read_csv(file_path, nrows=100)
        else:
            df = pd.read_excel(file_path, nrows=100)
        
        # 自动识别规则列：排除目标列、ID列，保留数值型或标准标记型的列
        exclude_patterns = ['label', 'target', 'overdue', 'y', 'flag', 'id', 'customer', 'date', 'time', 'score', 'predict', 'prob']
        
        rule_candidates = []
        for col in df.columns:
            col_lower = col.lower()
            if any(p in col_lower for p in exclude_patterns):
                continue
            
            # 检查是否是规则候选列
            if df[col].dtype in ['int64', 'float64']:
                # 数值型列可能是规则分数或分数阈值
                unique_ratio = df[col].nunique() / len(df)
                if unique_ratio > 0.01:  # 至少有1%的不同值
                    rule_candidates.append({
                        'name': col,
                        'type': 'numeric',
                        'unique_count': df[col].nunique(),
                        'sample_values': df[col].dropna().head(5).tolist(),
                    })
            elif df[col].dtype == 'object' or df[col].dtype == 'bool':
                # 标记型列可能是命中标记
                unique_vals = df[col].dropna().unique()
                unique_set = set(str(v) for v in unique_vals)
                if unique_set.issubset({'0', '1', 'True', 'False', 'true', 'false', 'yes', 'no', 'Y', 'N'}):
                    rule_candidates.append({
                        'name': col,
                        'type': 'flag',
                        'unique_values': list(unique_set),
                    })
        
        return jsonify({
            'rule_candidates': rule_candidates,
            'all_columns': list(df.columns),
        })
    
    except Exception as e:
        return jsonify({'error': f'读取文件失败：{str(e)}'}), 500
