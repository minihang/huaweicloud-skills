#!/usr/bin/env python3
"""Local JSON training journal. Python 3.9+, standard library only; JSON stdin/stdout."""
import argparse
import copy
import datetime as dt
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from contextlib import contextmanager


def number(v, lo, hi, label):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
        raise ValueError('数值非法: ' + label)
    return v


def validate_set_row(row, exercises):
    """Validate a set row against the session's current plan. Returns the exercise entry."""
    if not isinstance(row.get('event_id'), str) or not row['event_id']: raise ValueError('event_id 必填')
    e = next((e for e in exercises if e['id'] == row.get('exercise_id')), None)
    if e is None: raise ValueError('未知动作: ' + str(row.get('exercise_id')))
    number(row.get('reps'), 0, 1000, 'reps')
    if not isinstance(row['reps'], int): raise ValueError('reps 必须是整数')
    if row.get('load_kg') is not None: number(row['load_kg'], 0, 1000, 'load_kg')
    if row.get('rpe') is not None: number(row['rpe'], 1, 10, 'rpe')
    if row.get('load_basis') != e['load_basis']: raise ValueError('计重口径与计划不一致')
    for key, allowed, default in [('safety', ('none', 'pain', 'urgent'), 'none'),
                                   ('quality', ('unknown', 'good', 'poor'), 'unknown'),
                                   ('kind', ('work', 'warmup'), 'work')]:
        if row.get(key, default) not in allowed: raise ValueError('取值非法: ' + key)
    if row.get('kind', 'work') == 'work' and e.get('planned_kind', 'work') != 'work':
        raise ValueError('正式组不能记在热身条目上: ' + str(row.get('exercise_id')) + '；请标 kind:warmup 或将条目 planned_kind 设为 work')
    if not isinstance(row.get('feeling', ''), str): raise ValueError('feeling 必须是文本')
    return e


def validate_plan(p):
    if not isinstance(p, dict) or not isinstance(p.get('days'), list) or not p['days']:
        raise ValueError('plan 需要非空 days')
    for day in p['days']:
        if not isinstance(day.get('exercises'), list) or not day['exercises']:
            raise ValueError('day 需要非空 exercises')
        ids = set()
        for e in day['exercises']:
            if not e.get('id') or e['id'] in ids or not e.get('name'):
                raise ValueError('动作需要唯一 id 和 name')
            ids.add(e['id'])
            number(e.get('sets'), 1, 20, 'sets')
            if not isinstance(e['sets'], int): raise ValueError('sets 必须是整数')
            for key in ('reps_min', 'reps_max'): number(e.get(key), 1, 100, key)
            if e['reps_min'] > e['reps_max']: raise ValueError('次数范围颠倒')
            number(e.get('rest_seconds'), 15, 600, 'rest_seconds')
            number(e.get('target_rpe'), 1, 10, 'target_rpe')
            if e.get('load_kg') is not None: number(e['load_kg'], 0, 1000, 'load_kg')
            if e.get('load_basis') not in ('total', 'per_hand', 'machine', 'bodyweight', 'assisted'):
                raise ValueError('load_basis 取值非法')
            # 热身条目：不进完成率分母与进阶评估；缺省视为正式条目（向后兼容）
            if e.get('planned_kind', 'work') not in ('work', 'warmup'):
                raise ValueError('planned_kind 取值非法: ' + str(e.get('planned_kind')))
            e['planned_kind'] = e.get('planned_kind', 'work')
    return p


def suggestion(s, e, row):
    # Conservative engineering heuristics, not scientifically validated prescriptions.
    if row.get('safety') == 'urgent':
        return {'action': 'stop_session', 'reason': '报告急性危险症状，停止训练并及时求助', 'load_kg': None}
    if row.get('safety') == 'pain':
        return {'action': 'stop_exercise', 'reason': '疼痛与一般肌肉疲劳分开处理；先澄清，不带痛加重', 'load_kg': None}
    if row.get('quality') == 'poor':
        return {'action': 'reduce_or_substitute', 'reason': '动作质量下降，先休息并降低负荷或替换动作', 'load_kg': None}
    if row.get('kind', 'work') == 'warmup':
        # 热身组不驱动正式组降载：低次数爬坡属正常设计，仅 RPE 超标才回调热身本身
        rpe = row.get('rpe')
        if rpe is not None and rpe > e['target_rpe'] + 1:
            return {'action': 'reduce_warmup_load', 'reason': '热身组强度超目标：下次下调热身重量，正式组安排不变', 'load_kg': row.get('load_kg')}
        return {'action': 'observe', 'reason': '热身组不驱动正式组调整', 'load_kg': row.get('load_kg')}
    rpe = row.get('rpe')
    if rpe is None:
        return {'action': 'observe', 'reason': '强度未知，保持或保守调整，询问还能规范完成几次', 'load_kg': row.get('load_kg')}
    if rpe > e['target_rpe'] + 1 or row['reps'] < e['reps_min']:
        load = row.get('load_kg')
        # 0 是合法负重（空杆/最轻配重）：is not None 判空，不用 falsy
        return {'action': 'reduce', 'load_kg': round(load * .95, 2) if load is not None and e['load_basis'] not in ('bodyweight', 'assisted') else None,
                'rest_seconds': min(600, e['rest_seconds'] + 30), 'reason': '高于目标强度或未达到次数下限；候选降载5%，按器械档位向保守方向取整'}
    return {'action': 'maintain', 'load_kg': row.get('load_kg'), 'rest_seconds': e['rest_seconds'],
            'reason': '先保持并观察；不因单组轻松自动加重量或重写长期计划'}


def active_work_rows(s):
    return [r for r in s['logs'] if r.get('kind', 'work') == 'work' and not r.get('superseded_by')]


def session_stats(s):
    rows = active_work_rows(s)
    # 分母只统计正式条目（planned_kind=work，缺省兼容旧数据）；热身条目不稀释完成率
    planned = sum(e['sets'] for e in s['plan']['exercises'] if e.get('planned_kind', 'work') == 'work')
    planned_original = sum(e['sets'] for e in s['original_plan']['exercises'] if e.get('planned_kind', 'work') == 'work')
    volume = {}
    for r in rows:
        key = r['exercise_id'] + ':' + r['load_basis']
        if r.get('load_kg') is not None and r['load_basis'] not in ('bodyweight', 'assisted'):
            volume[key] = volume.get(key, 0) + r['load_kg'] * r['reps']
    known = [r['rpe'] for r in rows if r.get('rpe') is not None]
    return {'work_sets': len(rows), 'planned_work_sets': planned,
            'planned_work_sets_original': planned_original,
            'completion_ratio': len(rows) / planned if planned else None,
            'completion_ratio_original': len(rows) / planned_original if planned_original else None,
            'volume_kg_reps_by_exercise_and_basis': volume,
            'mean_reported_rpe': sum(known)/len(known) if known else None,
            'rpe_coverage': len(known)/len(rows) if rows else 0}


def normalized_date(s):
    """读取侧日期容错归一化：遗留日志可能是 ISO 基本格式（如 20260101），排序前统一。"""
    try: return dt.date.fromisoformat(s).isoformat()
    except (ValueError, TypeError): return s


def _parse_date(s):
    try: return dt.date.fromisoformat(normalized_date(s))
    except (ValueError, TypeError): return None


def _entry_aggregate(rows):
    """单个 entry 的聚合摘要：让进步曲线直接可读，AI 不必回 inspect 全库。"""
    known = [r['rpe'] for r in rows if r.get('rpe') is not None]
    loads = [r.get('load_kg') for r in rows]
    return {'work_sets': len(rows),
            'mean_reps': round(sum(r['reps'] for r in rows) / len(rows), 2),
            'mean_rpe': round(sum(known) / len(known), 2) if known else None,
            'rpe_known_ratio': round(len(known) / len(rows), 2) if rows else 0,
            'load_kg': loads[0] if loads and all(l == loads[0] for l in loads) else None}


def review(db):
    # 可调间隔阈值：相邻可比训练间隔超过该天数则阻断进阶候选（P2）；非法值回落默认
    gap_limit = db['profile'].get('long_gap_days', 21)
    if isinstance(gap_limit, bool) or not isinstance(gap_limit, int) or not 1 <= gap_limit <= 365:
        gap_limit = 21
    sessions = sorted([s for s in db['sessions'] if s['status'] == 'completed'], key=lambda s: normalized_date(s['date']))
    # 读取侧诚实性：日期不可解析（通常为手改遗留数据）时显式警告，不静默吞掉排序/间隔异常
    date_warnings = ['unparseable_date: ' + s['id'] for s in sessions if _parse_date(s['date']) is None]
    groups = {}
    for s in sessions:
        rows_by_ex = {}
        for r in s['logs']:
            if r.get('kind', 'work') == 'work' and not r.get('superseded_by'):
                rows_by_ex.setdefault(r['exercise_id'], []).append(r)
        for e in s['plan']['exercises']:
            if e.get('planned_kind', 'work') != 'work': continue  # 热身条目不进进阶评估
            rows = rows_by_ex.get(e['id'])
            if rows:
                signature = (e['id'], e['load_basis'], e['reps_min'], e['reps_max'], e['target_rpe'])
                groups.setdefault(signature, []).append((s['id'], s['date'], s['plan'].get('name'), rows, e))
    results = []
    for sig, entries in groups.items():
        recent = entries[-3:]
        blocked = []
        # gap_days：相邻 entry 的间隔天数（首条为 null）；供 AI 判断恢复周期与中断
        parsed = [_parse_date(x[1]) for x in recent]
        gap_days = [None] + [(b - a).days if a is not None and b is not None else None
                             for a, b in zip(parsed, parsed[1:])]
        if len(recent) < 3:
            decision = 'insufficient_data'
            blocked.append('insufficient_data')
        else:
            if any(r.get('quality') == 'poor' for _, _, _, rows, _ in recent for r in rows):
                blocked.append('quality_poor')
            if any(r.get('rpe') is None for _, _, _, rows, _ in recent for r in rows):
                blocked.append('rpe_unknown')
            if any(r.get('safety', 'none') != 'none' for _, _, _, rows, _ in recent for r in rows):
                blocked.append('safety_flag')
            if any(len(rows) < e['sets'] for _, _, _, rows, e in recent):
                blocked.append('sets_incomplete')
            if any(r['reps'] < e['reps_max'] for _, _, _, rows, e in recent for r in rows):
                blocked.append('reps_below_max')
            if any(r.get('rpe') is not None and r['rpe'] > e['target_rpe']
                   for _, _, _, rows, e in recent for r in rows):
                blocked.append('rpe_above_target')
            if any(g is not None and g > gap_limit for g in gap_days):
                blocked.append('long_gap')
            loads = {r.get('load_kg') for _, _, _, rows, _ in recent for r in rows}
            # 自重/辅助/档位机器（load_kg 全为 null）不以公斤追踪负荷：
            # 豁免 same_load 检查，以 load_not_tracked 说明；档位进阶由宿主 AI 结合 extra 档位判断
            not_tracked = sig[1] in ('bodyweight', 'assisted') or (sig[1] == 'machine' and loads <= {None})
            same_load = not_tracked or (len(loads) == 1 and None not in loads)
            if not same_load: blocked.append('load_varied')
            hard = all(any(r.get('rpe') is not None and r['rpe'] > e['target_rpe'] + 1 for r in rows)
                       for _, _, _, rows, e in recent)
            # 进步可见性（P3）：三次 mean_reps 严格递增且均在次数区间内，
            # 且除 reps_below_max 外无其他阻断（中断 long_gap、质量、安全问题会压制进步结论）
            aggs = [_entry_aggregate(x[3]) for x in recent]
            means = [a['mean_reps'] for a in aggs]
            in_range = all(e['reps_min'] <= means[i] <= e['reps_max']
                           for i, (_, _, _, _, e) in enumerate(recent))
            progressing = (means[0] < means[1] < means[2] and in_range
                           and set(blocked) <= {'reps_below_max'})
            decision = ('consider_smallest_load_increment' if not blocked else
                        'review_recovery_and_reduce' if hard else
                        'reps_progressing_in_range' if progressing else
                        'maintain_and_observe')
        dates = [x[1] for x in recent]
        results.append({'exercise_id': sig[0], 'load_basis': sig[1], 'decision': decision,
                        'entries': [{'session_id': x[0], 'date': x[1], 'day': x[2],
                                     'gap_days': gap_days[i], **_entry_aggregate(x[3])}
                                    for i, x in enumerate(recent)],
                        'blocked_by': blocked,
                        'load_not_tracked': sig[1] in ('bodyweight', 'assisted') or (len(recent) == 3 and {r.get('load_kg') for _, _, _, rows, _ in recent for r in rows} <= {None}),
                        'same_day_sessions': len(dates) != len(set(dates))})
    out = {'completed_sessions': len(sessions), 'exercise_reviews': results,
           'long_gap_days_used': gap_limit,
           'note': '仅供候选决策；结合日期间隔（gap_days）、目标、疼痛、依从性和动作版本审核后，以plan保存新版本；不自动改历史'}
    if date_warnings:
        out['warnings'] = date_warnings
    return out


@contextmanager
def locked(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = Path(str(path) + '.lock')
    try: lock.mkdir()
    except FileExistsError: raise ValueError('日志被占用；稍后重试；不要移除其他写入者的锁')
    try: yield
    finally: lock.rmdir()


def save(path, db):
    fd, temp = tempfile.mkstemp(prefix=path.name+'.', dir=path.parent)
    try:
        os.chmod(temp, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(db, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.flush(); os.fsync(f.fileno())
        if path.exists():
            backup = Path(str(path)+'.bak')
            backup.write_bytes(path.read_bytes()); backup.chmod(0o600)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def normalize_row(row):
    """填充脚本默认值，使省略默认字段与显式传默认值的行在幂等比对中等价。"""
    out = copy.deepcopy(row)
    for key, default in (('safety', 'none'), ('quality', 'unknown'), ('kind', 'work')):
        out.setdefault(key, default)
    out.setdefault('feeling', '')
    return out


def operate(db, op, p):
    if op == 'inspect': return db
    if op == 'review': return review(db)
    if op == 'profile':
        # 传值更新；显式传 null 表示删除该键（列表字段走整表替换，更新前应先 inspect 合并）
        for k, v in p.items():
            if v is None: db['profile'].pop(k, None)
            else: db['profile'][k] = v
        if 'target_rpe' in db['profile']: number(db['profile']['target_rpe'], 1, 10, 'target_rpe')
        if 'long_gap_days' in db['profile']:
            v = db['profile']['long_gap_days']
            if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 365:
                raise ValueError('long_gap_days 需为 1–365 的整数天（相邻可比训练间隔阈值，缺省 21；设 null 删除恢复默认）')
        return db['profile']
    if op == 'plan':
        validate_plan(p)
        version = {'version': len(db['plans'])+1, 'created_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'plan': p}
        db['plans'].append(version); return version
    if op == 'start':
        if not db['plans']: raise ValueError('请先保存 plan')
        if any(s['status']=='active' for s in db['sessions']): raise ValueError('请先结束当前活动训练')
        if not isinstance(p.get('session_id'), str) or not p['session_id']: raise ValueError('session_id 必填')
        if not isinstance(p.get('date'), str): raise ValueError('date 必填（用户当地日期，ISO 格式）')
        if any(s['id']==p['session_id'] for s in db['sessions']): raise ValueError('session_id 已存在')
        p['date'] = dt.date.fromisoformat(p['date']).isoformat()  # 归一化为 YYYY-MM-DD，保证 review 排序一致
        version = p.get('plan_version', len(db['plans']))
        if isinstance(version, bool) or not isinstance(version, int) or not 1 <= version <= len(db['plans']):
            raise ValueError('plan_version 不存在: ' + str(version))
        days = db['plans'][version-1]['plan']['days']
        day_index = p.get('day_index', 0)
        if isinstance(day_index, bool) or not isinstance(day_index, int) or not 0 <= day_index < len(days):
            raise ValueError('day_index 超出范围 0..' + str(len(days)-1))
        day = days[day_index]
        s = {'id':p['session_id'], 'date':p['date'], 'status':'active', 'plan_version':version, 'original_plan':copy.deepcopy(day), 'plan':copy.deepcopy(day), 'changes':[], 'logs':[]}
        db['sessions'].append(s); return s
    if op == 'purge':
        if p.get('confirm') != 'PURGE': raise ValueError('purge 需要显式确认: {"confirm":"PURGE"}')
        scope = p.get('scope', 'all')
        if scope == 'all':
            db['profile'] = {}; db['plans'] = []; db['sessions'] = []
            removed = 'all'
        elif scope == 'sessions':
            ids = p.get('session_ids')
            if not isinstance(ids, list) or not ids: raise ValueError('scope=sessions 需要 session_ids 列表')
            existing = {x['id'] for x in db['sessions']}
            unknown = [i for i in ids if i not in existing]
            if unknown: raise ValueError('未知 session: ' + ', '.join(map(str, unknown)))
            db['sessions'] = [x for x in db['sessions'] if x['id'] not in set(ids)]
            removed = ids
        else:
            raise ValueError('scope 取值非法: ' + str(scope))
        return {'purged': removed, 'note': '主日志已清除；.bak 备份随本次写入一并删除；外部同步副本需用户自行处理'}
    s = next((s for s in db['sessions'] if s['id']==p.get('session_id')), None)
    if s is None: raise ValueError('未知 session')
    if op == 'revise':
        # 更正记录允许在训练结束后进行（用户常在 finish 后发现记错）；log/amend/finish 仅限活动训练
        target_id = p.get('event_id')
        if not isinstance(target_id, str) or not target_id: raise ValueError('event_id 必填')
        target = next((r for r in s['logs'] if r['event_id']==target_id), None)
        if target is None: raise ValueError('待更正的 event_id 不存在')
        row = copy.deepcopy(p['set'])
        row['revises'] = target_id
        if target.get('superseded_by'):
            existing = next((r for r in s['logs'] if r['event_id']==target['superseded_by']), None)
            if existing is not None and normalize_row(existing) == normalize_row(row):
                return {'duplicate': True, 'revised': target_id, 'effective': existing['event_id'], 'stats': session_stats(s)}
            raise ValueError('该事件已被更正为 ' + target['superseded_by'] + '；请更正链尾事件')
        e = validate_set_row(row, s['plan']['exercises'])
        if row['event_id'] == target_id: raise ValueError('修订事件需要新的 event_id')
        if next((r for r in s['logs'] if r['event_id']==row['event_id']), None): raise ValueError('event_id 已存在')
        target['superseded_by'] = row['event_id']
        s['logs'].append(row)
        return {'revised': target_id, 'effective': row['event_id'], 'stats': session_stats(s)}
    if s['status'] != 'active': raise ValueError('session 已结束')
    if op == 'amend':
        validate_plan({'days':[p['day']]})
        if not p.get('reason'): raise ValueError('amend 需要 reason')
        old = {e['id']:e for e in s['plan']['exercises']}
        new = {e['id']:e for e in p['day']['exercises']}
        for r in s['logs']:
            eid = r['exercise_id']
            if eid not in new or new[eid]['load_basis'] != old.get(eid, {}).get('load_basis'):
                raise ValueError('须保留已记录动作及其计重口径；替换动作请使用新 id')
            if r.get('kind', 'work') == 'work' and new[eid].get('planned_kind', 'work') != 'work':
                raise ValueError('已记录正式组的条目不能降级为热身（planned_kind）：会虚增完成率；如需纠正请先 revise 对应组')
        for eid, exercise in new.items():
            done = sum(r['exercise_id']==eid and r.get('kind','work')=='work' and not r.get('superseded_by') for r in s['logs'])
            if exercise['sets'] < done: raise ValueError('sets 不能低于已完成正式组数: ' + eid)
        s['changes'].append({'reason':p['reason'], 'previous':copy.deepcopy(s['plan'])})
        s['plan'] = p['day']; return s
    if op == 'finish':
        if p.get('status','completed') not in ('completed','aborted'): raise ValueError('status 取值非法')
        s['status']=p.get('status','completed'); s['summary']=p.get('summary','')
        return session_stats(s)
    if op == 'log':
        row = copy.deepcopy(p['set'])
        e = validate_set_row(row, s['plan']['exercises'])
        previous = next((r for r in s['logs'] if r['event_id']==row['event_id']),None)
        if previous:
            if normalize_row(previous) != normalize_row(row):
                raise ValueError('event_id 冲突；内容实质不同，不得覆盖历史，改用 revise')
            return {'duplicate':True, 'stats':session_stats(s)}
        if row.get('revises'): raise ValueError('revises 只能由 revise 命令写入')
        s['logs'].append(row)
        return {'suggestion':suggestion(s,e,row), 'stats':session_stats(s)}
    raise ValueError('未知操作')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=['inspect','profile','plan','start','amend','log','revise','finish','review','purge'])
    parser.add_argument('--data',required=True,help='Absolute durable journal JSON path; never inside skill repository')
    args=parser.parse_args()
    path=Path(args.data).expanduser()
    try:
        # 编码硬约定：stdin/stdout/stderr 一律 UTF-8，与平台 locale 无关（中文内容在 cp936 等 locale 下不受影响）
        for stream in (sys.stdout, sys.stderr):
            if stream is not None: stream.reconfigure(encoding='utf-8')
        if not path.is_absolute(): raise ValueError('数据路径必须是绝对路径')
        if Path(__file__).resolve().parents[1] in path.resolve().parents: raise ValueError('个人数据不得放进技能目录')
        p = {} if args.operation in ('inspect','review') else json.loads(sys.stdin.buffer.read().decode('utf-8'))
        if not isinstance(p,dict): raise ValueError('输入必须是 JSON 对象')
        with locked(path):
            db=json.loads(path.read_text('utf-8')) if path.exists() else {'schema_version':1,'profile':{},'plans':[],'sessions':[]}
            if db.get('schema_version') != 1: raise ValueError('不支持的 schema 版本；不覆盖写入')
            out=operate(db,args.operation,p)
            if args.operation not in ('inspect','review'):
                save(path,db)
                if args.operation == 'purge':
                    bak = Path(str(path)+'.bak')
                    if bak.exists(): bak.unlink()
        print(json.dumps(out,ensure_ascii=False,allow_nan=False))
    except (ValueError, KeyError, TypeError, IndexError, OSError) as exc:
        print(json.dumps({'error':str(exc)},ensure_ascii=False),file=sys.stderr); sys.exit(2)

if __name__=='__main__': main()
