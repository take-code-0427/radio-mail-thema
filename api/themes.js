import { neon } from '@neondatabase/serverless';

function todayInTokyo() {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Tokyo',
    year: 'numeric', month: '2-digit', day: '2-digit'
  }).format(new Date());
}

export default async function handler(req, res) {
  if (req.method !== 'GET') {
    res.setHeader('Allow', 'GET');
    return res.status(405).json({ error: 'Method not allowed' });
  }
  if (!process.env.DATABASE_URL) {
    return res.status(503).json({ error: 'DATABASE_URL is not configured' });
  }

  const date = typeof req.query.date === 'string' ? req.query.date : todayInTokyo();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    return res.status(400).json({ error: 'date must be YYYY-MM-DD' });
  }

  try {
    const sql = neon(process.env.DATABASE_URL);
    const rows = await sql`
      SELECT
        station_id,
        station_name,
        program_name,
        start_at,
        end_at,
        theme,
        program_url,
        message_url,
        fetched_at
      FROM radio_themes
      WHERE broadcast_date = ${date}
      ORDER BY start_at ASC, station_name ASC
    `;
    res.setHeader('Cache-Control', 's-maxage=300, stale-while-revalidate=3600');
    return res.status(200).json({ date, programs: rows });
  } catch (error) {
    console.error(error);
    return res.status(500).json({ error: 'Failed to load themes' });
  }
}
