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

const THEME_PATTERNS = [
  /(?:今日|本日)?(?:の)?(?:メッセージ|メール|投稿)?テーマ\s*[：:]\s*[「『]?(.+?)(?:[」』]|$|\n)/i,
  /(?:メッセージ|メール)募集\s*[：:]\s*[「『]?(.+?)(?:[」』]|$|\n)/i,
  /(?:お題|募集テーマ)\s*[：:]\s*[「『]?(.+?)(?:[」』]|$|\n)/i,
];

function todayJst() {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Tokyo', year: 'numeric', month: '2-digit', day: '2-digit'
  }).format(new Date());
}

function compactDate(date) { return date.replaceAll('-', ''); }
function clean(value) { return String(value ?? '').replace(/<br\s*\/?>/gi, '\n').replace(/<[^>]+>/g, ' ').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/[ \t]+/g, ' ').replace(/\n+/g, '\n').trim(); }
function array(value) { return value == null ? [] : Array.isArray(value) ? value : [value]; }
function extractTheme(...values) {
  const text = values.filter(Boolean).join('\n');
  for (const pattern of THEME_PATTERNS) {
    const match = text.match(pattern);
    if (!match) continue;
    const theme = match[1].trim().replace(/^[『「"']|[』」"']$/g, '').split(/\n|(?:メール|メッセージ|投稿)(?:は|を|で|まで)/)[0].trim();
    if (theme.length >= 2 && theme.length <= 180) return theme;
  }
  return null;
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
          ${p.theme}, ${p.description}, ${p.programUrl}, ${null}, ${p.sourceUrl}, NOW()
        )
        ON CONFLICT (broadcast_date, station_id, start_at, program_name)
        DO UPDATE SET station_name=EXCLUDED.station_name, end_at=EXCLUDED.end_at,
          theme=EXCLUDED.theme, description=EXCLUDED.description, program_url=EXCLUDED.program_url,
          source_url=EXCLUDED.source_url, fetched_at=NOW()
      `;
    }

    return res.status(200).json({ date, saved: programs.length, themes: programs.filter(p => p.theme).length });
  } catch (error) {
    console.error(error);
    return res.status(500).json({ error: 'Collection failed', detail: String(error?.message ?? error) });
  }
}
