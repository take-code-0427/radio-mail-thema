import { neon } from '@neondatabase/serverless';
import { XMLParser } from 'fast-xml-parser';

const TARGET_STATIONS = {
  TBS: 'TBSラジオ',
  QRR: '文化放送',
  LFR: 'ニッポン放送',
  FMT: 'TOKYO FM',
  FMJ: 'J-WAVE',
  JORF: 'ラジオ日本',
};

const SUBMISSION_HINT_RE = /(?:メッセージ|メール|お便り|投稿|アンケート|ワンコメ|送って|お送り|お寄せ|募集中|募集|参加|フォーム)/i;

const STRONG_THEME_PATTERNS = [
  /(?:今日|本日)?(?:の)?メールテーマ\s*[＝=]\s*「タネ」\s*は\s*[【「『]\s*(.+?)\s*[】」』]/i,
  /本日の議題は[^。\n]{0,100}?[【「『]\s*(.+?)\s*[】」』]/i,
  /(?:(?:今日|本日|けさ|今朝|今週|今夜)(?:の)?)?(?:メッセージ|メール|投稿)テーマ\s*(?:は|[＝=:：])\s*[、,\s]*[「『【]?\s*(.+?)(?:[」』】]|$|\n)/i,
  /(?:募集テーマ|お題)\s*(?:は|[＝=:：])\s*[、,\s]*[「『【]?\s*(.+?)(?:[」』】]|$|\n)/i,
  /(?:本日|今日)?(?:の)?議題\s*(?:は|[＝=:：])\s*[「『【]\s*(.+?)\s*[」』】]/i,
];

const GENERIC_THEME_LABEL_RE = /(?:(?:今日|本日|けさ|今朝|今週|今夜|月曜(?:日)?|火曜(?:日)?|水曜(?:日)?|木曜(?:日)?|金曜(?:日)?|土曜(?:日)?|日曜(?:日)?)(?:の)?)?テーマ\s*(?:は|[＝=:：])/gi;
const GENERIC_THEME_EXCLUDED_PREFIXES = ['選曲', '楽曲', '音楽', '特集', 'コーナー', '企画'];

function todayJst() {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Tokyo', year: 'numeric', month: '2-digit', day: '2-digit'
  }).format(new Date());
}

function compactDate(date) { return date.replaceAll('-', ''); }
function clean(value) { return String(value ?? '').replace(/<br\s*\/?>/gi, '\n').replace(/<[^>]+>/g, ' ').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/[ \t]+/g, ' ').replace(/\n+/g, '\n').trim(); }
function array(value) { return value == null ? [] : Array.isArray(value) ? value : [value]; }

function normalizeTheme(value) {
  const theme = String(value ?? '')
    .trim()
    .replace(/^[\s、,。．・:：=＝『』「」【】\[\]"']+|[\s、,。．・:：=＝『』「」【】\[\]"']+$/g, '')
    .split(/\n|(?:メール|メッセージ|投稿)(?:は|を|で|まで)/)[0]
    .replace(/[♪！!。．、,]+$/g, '')
    .trim();
  if (theme.length < 2 || theme.length > 120) return null;
  if (/https?:\/\/|\S+@\S+/i.test(theme)) return null;
  return theme;
}

function genericThemeCandidate(text, match) {
  const prefix = text.slice(Math.max(0, match.index - 12), match.index);
  if (GENERIC_THEME_EXCLUDED_PREFIXES.some(word => prefix.includes(word))) return null;

  const context = text.slice(Math.max(0, match.index - 220), Math.min(text.length, match.index + match[0].length + 520));
  if (!SUBMISSION_HINT_RE.test(context)) return null;

  const tail = text.slice(match.index + match[0].length, match.index + match[0].length + 180).replace(/^[\s、,：:]+/, '');
  const quoted = tail.match(/[「『【]\s*([^」』】\n]{2,120}?)\s*[」』】]/);
  if (quoted && quoted.index <= 40) return normalizeTheme(quoted[1]);

  const raw = tail.split(/\n|[。．！!]/, 1)[0];
  return normalizeTheme(raw);
}

function extractTheme(...values) {
  const text = values.filter(Boolean).join('\n');

  for (const pattern of STRONG_THEME_PATTERNS) {
    const match = text.match(pattern);
    if (!match) continue;
    const theme = normalizeTheme(match[1]);
    if (theme) return theme;
  }

  GENERIC_THEME_LABEL_RE.lastIndex = 0;
  for (const match of text.matchAll(GENERIC_THEME_LABEL_RE)) {
    const theme = genericThemeCandidate(text, match);
    if (theme) return theme;
  }
  return null;
}

function extractMessageUrl(...values) {
  const text = values.filter(Boolean).join('\n');
  const mailto = text.match(/mailto:([^\s"'<>]+)/i);
  if (mailto) return `mailto:${mailto[1]}`;
  const email = text.match(/(?<![\w.-])([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})(?![\w.-])/i);
  if (email) return `mailto:${email[1]}`;
  const form = text.match(/https?:\/\/[^\s"'<>]+/gi)?.find(url => /message|mail|form/i.test(url));
  return form ?? null;
}
function radikoDateTime(value) {
  const s = String(value);
  const iso = `${s.slice(0,4)}-${s.slice(4,6)}-${s.slice(6,8)}T${s.slice(8,10)}:${s.slice(10,12)}:${s.slice(12,14)}+09:00`;
  return new Date(iso);
}

export default async function handler(req, res) {
  if (!process.env.DATABASE_URL) return res.status(503).json({ error: 'DATABASE_URL is not configured' });
  const date = typeof req.query.date === 'string' ? req.query.date : todayJst();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return res.status(400).json({ error: 'date must be YYYY-MM-DD' });

  try {
    const sourceUrl = `https://radiko.jp/v3/program/date/${compactDate(date)}/JP13.xml`;
    const response = await fetch(sourceUrl, { headers: { 'User-Agent': 'radio-mail-thema/0.2' } });
    if (!response.ok) throw new Error(`radiko returned ${response.status}`);
    const xml = await response.text();
    const parser = new XMLParser({ ignoreAttributes: false, attributeNamePrefix: '@_' });
    const doc = parser.parse(xml);
    const stations = array(doc?.radiko?.stations?.station ?? doc?.stations?.station);
    const programs = [];

    for (const station of stations) {
      const stationId = station?.['@_id'] ?? station?.id;
      if (!TARGET_STATIONS[stationId]) continue;
      const stationName = clean(station?.name) || TARGET_STATIONS[stationId];
      for (const prog of array(station?.progs?.prog ?? station?.prog)) {
        const ft = prog?.['@_ft']; const to = prog?.['@_to'];
        if (!ft || !to) continue;
        const title = clean(prog?.title) || '(番組名なし)';
        const desc = clean(prog?.desc); const info = clean(prog?.info);
        programs.push({
          broadcastDate: date,
          stationId,
          stationName,
          programName: title,
          startAt: radikoDateTime(ft),
          endAt: radikoDateTime(to),
          theme: extractTheme(title, desc, info),
          description: `${desc}${info ? `\n${info}` : ''}`.slice(0, 5000) || null,
          programUrl: clean(prog?.url) || null,
          messageUrl: extractMessageUrl(desc, info),
          sourceUrl,
        });
      }
    }

    const sql = neon(process.env.DATABASE_URL);
    for (const p of programs) {
      await sql`
        INSERT INTO radio_themes (
          broadcast_date, station_id, station_name, program_name, start_at, end_at,
          theme, description, program_url, message_url, source_url, fetched_at
        ) VALUES (
          ${p.broadcastDate}, ${p.stationId}, ${p.stationName}, ${p.programName}, ${p.startAt}, ${p.endAt},
          ${p.theme}, ${p.description}, ${p.programUrl}, ${p.messageUrl}, ${p.sourceUrl}, NOW()
        )
        ON CONFLICT (broadcast_date, station_id, start_at, program_name)
        DO UPDATE SET station_name=EXCLUDED.station_name, end_at=EXCLUDED.end_at,
          theme=EXCLUDED.theme, description=EXCLUDED.description, program_url=EXCLUDED.program_url,
          message_url=EXCLUDED.message_url, source_url=EXCLUDED.source_url, fetched_at=NOW()
      `;
    }

    return res.status(200).json({ date, saved: programs.length, themes: programs.filter(p => p.theme).length });
  } catch (error) {
    console.error(error);
    return res.status(500).json({ error: 'Collection failed', detail: String(error?.message ?? error) });
  }
}
