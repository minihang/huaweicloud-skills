#!/usr/bin/env python3
"""Progress-share SVG card from the local training journal. Python 3.9+, standard
library only. Reads the journal, never writes it. Output is a self-contained SVG
(no external fonts/assets); CJK text relies on the viewer's system font stack.

Usage:
  python card.py --data <journal.json> --output <card.svg> [--exercise <id>] [--title <text>]

Exit code 2 with a JSON error on stderr when input is unusable (mirrors coach.py).
Honesty rules carried over: no fabricated RPE averages, no invented points; fewer
than 2 completed sessions for the exercise is an error, not an empty card.
"""
import argparse
import datetime as dt
import json
import math
import sys
from pathlib import Path

W, H = 1080, 1440
ACCENT = '#FF2442'
INK = '#222222'
MUTED = '#888888'
GRID = '#EEEEEE'
FONT = "'PingFang SC','Microsoft YaHei','Noto Sans CJK SC',sans-serif"
LONG_GAP_DAYS = 21  # 与 coach.py review 缺省阈值一致；仅作提示，不阻断


def esc(t):
    return (str(t).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            .replace('"', '&quot;'))


def fmt_num(v):
    """1 位小数，去掉多余尾零：9.0 -> '9'，8.5 -> '8.5'。"""
    s = ('%.1f' % v).rstrip('0').rstrip('.')
    return s or '0'


def normalized_date(s):
    """读取侧日期容错归一化（与 coach.py 一致）：遗留 ISO 基本格式也按真实日期排序。"""
    try:
        return dt.date.fromisoformat(s).isoformat()
    except (ValueError, TypeError):
        return s


def _unparseable(s):
    try:
        dt.date.fromisoformat(s)
        return False
    except (ValueError, TypeError):
        return True


def collect_series(db, exercise_id=None):
    """从已完成训练中聚合某动作的逐次表现。不传 id 时自动选出现次数最多的正式动作。

    返回 (exercise_id, meta, points)：meta 含 name/reps_min/reps_max；每个 point 为
    一次训练的 date/session_id/work_sets/mean_reps/RPE 记录情况与恒定负荷（如有）。
    superseded 行与热身行不计；数据不足 2 点时抛 ValueError。
    """
    sessions = sorted([s for s in db.get('sessions', []) if s.get('status') == 'completed'],
                      key=lambda s: normalized_date(s.get('date', '')))
    meta = {}
    counts = {}
    for s in sessions:
        for e in (s.get('plan', {}).get('exercises') or []):
            if e.get('planned_kind', 'work') != 'work':
                continue
            meta.setdefault(e['id'], {'name': e.get('name') or e['id'],
                                      'reps_min': e.get('reps_min'),
                                      'reps_max': e.get('reps_max')})
            counts[e['id']] = counts.get(e['id'], 0) + 1
    if not counts:
        raise ValueError('没有已完成的正式训练记录，无法生成卡片')
    if exercise_id is None:
        # 按实际可画的训练数（points）排序，而非 plan 出现次数：
        # 出现 3 次但只记过 1 次的动作不应抢走记录完整的动作
        def score(eid):
            n = 0
            for s in sessions:
                if any(r.get('exercise_id') == eid and r.get('kind', 'work') == 'work'
                       and not r.get('superseded_by') for r in (s.get('logs') or [])):
                    n += 1
            return (n, counts[eid], eid)
        exercise_id = max(counts, key=score)
    if exercise_id not in meta:
        raise ValueError('该动作没有已完成的正式训练记录: ' + str(exercise_id))
    points = []
    for s in sessions:
        rows = [r for r in (s.get('logs') or [])
                if r.get('exercise_id') == exercise_id
                and r.get('kind', 'work') == 'work'
                and not r.get('superseded_by')]
        if not rows:
            continue
        known = [r['rpe'] for r in rows if r.get('rpe') is not None]
        loads = {r.get('load_kg') for r in rows}
        points.append({'date': normalized_date(s.get('date', '')),
                       'session_id': s.get('id', ''),
                       'work_sets': len(rows),
                       'mean_reps': sum(r['reps'] for r in rows) / len(rows),
                       'rpe_known': len(known),
                       'rpe_total': len(rows),
                       'load_kg': (next(iter(loads)) if len(loads) == 1 else None)})
    if len(points) < 2:
        raise ValueError('动作 "' + str(meta[exercise_id]['name']) + '" 的已完成训练不足 2 次，先继续记录再生成卡片')
    return exercise_id, meta[exercise_id], points


def _trend_and_gaps(points):
    """保守文案：只描述记录趋势，不给进阶建议。

    与 coach.py review 的 long_gap 语义一致：含长时间中断的进步不可比，
    不得宣称"逐次进步"。日期不可解析时显式警告而非静默跳过。"""
    reps = [p['mean_reps'] for p in points]
    gaps = []
    unparseable = []
    for a, b in zip(points, points[1:]):
        try:
            d = (dt.date.fromisoformat(b['date']) - dt.date.fromisoformat(a['date'])).days
        except ValueError:
            unparseable.append(b['date'])
            continue
        if d > LONG_GAP_DAYS:
            gaps.append(d)
    if unparseable:
        trend = '部分日期无法解析，间隔与趋势仅供参考'
    elif gaps:
        trend = '跨段记录（含长时间中断），进步不可比'
    elif reps[-1] > reps[0] and all(b >= a for a, b in zip(reps, reps[1:])):
        trend = '次数区间内逐次进步'
    elif reps[-1] >= reps[0]:
        trend = '整体持平，持续记录中'
    else:
        trend = '近期次数回落，结合恢复与 RPE 解读'
    loads = [p['load_kg'] for p in points]
    # null 负荷（档位/自重）不是"负荷变化"，是无公斤追踪，不附注
    known_loads = [l for l in loads if l is not None]
    load_varied = len(known_loads) >= 2 and len(set(known_loads)) > 1
    if not unparseable and not gaps and load_varied:
        trend += '；负荷有变化，结合重量解读'
    return trend, gaps


def render_svg(points, meta, title=None):
    """纯函数：数据点 → SVG 字符串。无 IO，便于测试。"""
    name = title or meta['name']
    reps = [p['mean_reps'] for p in points]
    lo = int(math.floor(min(reps))) - 1
    hi = int(math.ceil(max(reps))) + 1
    if hi - lo < 2:
        hi = lo + 2
    px0, px1, py0, py1 = 110, 970, 400, 920

    def x(i):
        return px0 + (px1 - px0) * (i / (len(points) - 1))

    def y(v):
        return py1 - (py1 - py0) * ((v - lo) / (hi - lo))

    parts = []
    parts.append('<?xml version="1.0" encoding="UTF-8"?>')
    parts.append('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
                 'viewBox="0 0 %d %d">' % (W, H, W, H))
    parts.append('<rect width="%d" height="%d" fill="#FFFFFF"/>' % (W, H))
    parts.append('<rect width="%d" height="12" fill="%s"/>' % (W, ACCENT))
    # 标题区
    parts.append('<rect x="64" y="120" width="12" height="64" fill="%s"/>' % ACCENT)
    parts.append('<text x="100" y="168" font-family="%s" font-size="52" font-weight="700" '
                 'fill="%s">%s</text>' % (FONT, INK, esc(name)))
    sub = '%d 次已完成训练' % len(points)
    if meta.get('reps_min') is not None and meta.get('reps_max') is not None:
        sub += ' · 目标次数 %s–%s' % (fmt_num(meta['reps_min']), fmt_num(meta['reps_max']))
    fixed_loads = {p['load_kg'] for p in points} - {None}
    if len(fixed_loads) == 1 and None not in {p['load_kg'] for p in points}:
        sub += ' · 负荷 %s kg' % fmt_num(next(iter(fixed_loads)))
    parts.append('<text x="100" y="228" font-family="%s" font-size="28" fill="%s">%s</text>'
                 % (FONT, MUTED, esc(sub)))
    parts.append('<line x1="64" y1="280" x2="%d" y2="280" stroke="%s" stroke-width="2"/>'
                 % (W - 64, GRID))
    # 图表标题
    parts.append('<text x="110" y="345" font-family="%s" font-size="30" font-weight="600" '
                 'fill="#444444">平均每次完成次数</text>' % FONT)
    # 网格与 Y 轴
    step = 1 if hi - lo <= 6 else 2
    ticks = list(range(lo, hi + 1, step))
    for t in ticks:
        gy = y(t)
        parts.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" stroke-width="1"/>'
                     % (px0, gy, px1, gy, GRID))
        parts.append('<text x="%d" y="%.1f" font-family="%s" font-size="22" fill="#AAAAAA" '
                     'text-anchor="end">%d</text>' % (px0 - 14, gy + 8, FONT, t))
    # 折线与数据点
    poly = ' '.join('%.1f,%.1f' % (x(i), y(r)) for i, r in enumerate(reps))
    parts.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="4" '
                 'stroke-linejoin="round" stroke-linecap="round"/>' % (poly, ACCENT))
    for i, r in enumerate(reps):
        parts.append('<circle cx="%.1f" cy="%.1f" r="7" fill="#FFFFFF" stroke="%s" '
                     'stroke-width="4"/>' % (x(i), y(r), ACCENT))
        parts.append('<text x="%.1f" y="%.1f" font-family="%s" font-size="26" font-weight="700" '
                     'fill="%s" text-anchor="middle">%s</text>'
                     % (x(i), y(r) - 18, FONT, '#333333', fmt_num(r)))
    # X 轴日期（点太多时隔点抽稀）
    skip = 1 if len(points) <= 7 else math.ceil(len(points) / 7)
    for i, p in enumerate(points):
        if i % skip and i != len(points) - 1:
            continue
        label = p['date'][5:10].replace('-', '/') if len(p['date']) >= 10 else p['date']
        parts.append('<text x="%.1f" y="958" font-family="%s" font-size="22" fill="#999999" '
                     'text-anchor="middle">%s</text>' % (x(i), FONT, esc(label)))
    # 统计块
    total_sets = sum(p['work_sets'] for p in points)
    known = sum(p['rpe_known'] for p in points)
    total = sum(p['rpe_total'] for p in points)
    stats = [(len(points), '已完成训练'), (total_sets, '累计正式组'),
             ('%d/%d' % (known, total), 'RPE 已记录组')]
    for i, (v, label) in enumerate(stats):
        cx = 110 + i * 340
        parts.append('<text x="%d" y="1068" font-family="%s" font-size="44" font-weight="700" '
                     'fill="%s">%s</text>' % (cx, FONT, INK, esc(v)))
        parts.append('<text x="%d" y="1108" font-family="%s" font-size="22" fill="%s">%s</text>'
                     % (cx, FONT, '#999999', esc(label)))
    # 趋势与中断提示
    trend, gaps = _trend_and_gaps(points)
    color = ACCENT if trend == '次数区间内逐次进步' else MUTED
    parts.append('<circle cx="118" cy="1222" r="8" fill="%s"/>' % color)
    parts.append('<text x="142" y="1232" font-family="%s" font-size="30" font-weight="600" '
                 'fill="%s">%s</text>' % (FONT, color, esc(trend)))
    if gaps:
        parts.append('<text x="110" y="1280" font-family="%s" font-size="24" fill="#C77414">记录期间有超过 %d 天的中断，跨段比较需谨慎</text>'
                     % (FONT, LONG_GAP_DAYS))
    # 页脚
    today = dt.date.today().isoformat()
    parts.append('<text x="540" y="1385" font-family="%s" font-size="22" fill="#BBBBBB" '
                 'text-anchor="middle">数据来自本地训练日志 · 仅描述记录趋势，不构成进阶建议 · 生成于 %s</text>'
                 % (FONT, today))
    parts.append('</svg>')
    return '\n'.join(parts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', required=True, help='训练日志 JSON 的绝对路径')
    parser.add_argument('--output', required=True, help='输出 SVG 文件路径')
    parser.add_argument('--exercise', default=None, help='动作 id；缺省自动选记录最多的正式动作')
    parser.add_argument('--title', default=None, help='自定义卡片标题；缺省用动作名')
    args = parser.parse_args()
    path = Path(args.data).expanduser()
    try:
        for stream in (sys.stdout, sys.stderr):
            if stream is not None:
                stream.reconfigure(encoding='utf-8')
        if not path.is_absolute():
            raise ValueError('数据路径必须是绝对路径')
        if Path(__file__).resolve().parents[1] in path.resolve().parents:
            raise ValueError('个人数据不得放进技能目录')
        if not path.exists():
            raise ValueError('训练日志不存在: ' + str(path))
        db = json.loads(path.read_text('utf-8'))
        if db.get('schema_version') != 1:
            raise ValueError('不支持的 schema 版本')
        _, meta, points = collect_series(db, args.exercise)
        svg = render_svg(points, meta, args.title)
        unparseable = [p['date'] for p in points if _unparseable(p['date'])]
        out = Path(args.output)
        if not out.is_absolute():
            raise ValueError('输出路径必须是绝对路径')
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(svg, encoding='utf-8')
        summary = {'card': str(out), 'exercise': meta['name'],
                   'sessions': len(points),
                   'rpe_known': sum(p['rpe_known'] for p in points),
                   'rpe_total': sum(p['rpe_total'] for p in points)}
        if unparseable:
            summary['warnings'] = ['unparseable_date: ' + d for d in unparseable]
        print(json.dumps(summary, ensure_ascii=False))
    except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(2)


if __name__ == '__main__':
    main()
