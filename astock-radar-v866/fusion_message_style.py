"""Shared text-only presentation. Does not change evidence or delivery policy."""
import re


def style_message(content):
    original = str(content)
    lines = original.replace('\r\n', '\n').splitlines()
    if not lines:
        return original
    # Preserve the date and make the timezone explicit, without microsecond noise.
    lines = [re.sub(r'(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(?:\.\d+)?\+08:00',
                    r'\1 \2（北京时间）', line).rstrip() for line in lines]
    heading = lines[0]
    if 'A股机会雷达' in heading:
        if '｜A股机会雷达 新版' in heading:
            heading = 'A股机会雷达 新版｜' + heading.replace('｜A股机会雷达 新版', '')
    elif heading.startswith('📅 新版｜'):
        heading = heading.replace('📅 新版｜', '📅 A股机会雷达 新版｜', 1)
    lines[0] = heading
    if len(lines) > 1 and lines[1]:
        lines.insert(1, '')
    formatted = re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()
    # Formatting must never turn a previously deliverable message into a failure.
    return formatted if len(formatted.encode('utf-8')) <= 1800 else original
