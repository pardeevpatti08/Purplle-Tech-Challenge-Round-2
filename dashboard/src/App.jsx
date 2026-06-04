import {
  Activity,
  Clock3,
  Map as MapIcon,
  Radio,
  RefreshCcw,
  TrendingUp,
  Users,
  Video,
} from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

const apiUrl =
  import.meta.env.VITE_API_URL ||
  `${window.location.protocol}//${window.location.hostname}:8000`;

async function fetchJson(path) {
  const response = await fetch(`${apiUrl}${path}`);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

function formatClock(seconds = 0) {
  const value = Math.max(Math.floor(seconds), 0);
  const minutes = Math.floor(value / 60);
  return `${String(minutes).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`;
}

function VideoFeed({ camera, elapsed }) {
  const videoRef = useRef(null);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !Number.isFinite(video.duration) || video.duration <= 0) return;
    const target = elapsed % video.duration;
    if (Math.abs(video.currentTime - target) > 1.5) video.currentTime = target;
    video.play().catch(() => {});
  }, [elapsed]);

  return (
    <article className="camera-feed">
      <video
        loop
        muted
        playsInline
        preload="metadata"
        ref={videoRef}
        src={`${apiUrl}${camera.video_url}`}
      />
      <div className="camera-label">
        <span><Radio size={12} /> LIVE</span>
        <strong>{camera.camera_id}</strong>
        <small>{camera.role.replaceAll('_', ' ')}</small>
      </div>
    </article>
  );
}

function LayoutHeatmap({ store }) {
  const scores = new Map((store.heatmap || []).map((zone) => [zone.zone_id, zone]));
  const width = store.floor_plan?.image_width || 1000;
  const height = store.floor_plan?.image_height || 1000;

  return (
    <section className="heatmap-panel">
      <div className="section-title">
        <MapIcon size={17} />
        <div>
          <h3>Live Layout Heatmap</h3>
          <span>{store.store_name}</span>
        </div>
      </div>
      <div className="layout-map">
        <img src={`${apiUrl}${store.layout_url}`} alt={`${store.store_name} layout`} />
        <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="xMidYMid meet" aria-label="Live zone heatmap">
          {store.zones.filter((zone) => zone.heatmap_enabled && zone.polygon.length > 2).map((zone) => {
            const heat = scores.get(zone.zone_id);
            const score = heat?.heat_score || 0;
            return (
              <g key={zone.zone_id}>
                <polygon
                  className="heat-zone"
                  points={zone.polygon.map((point) => point.join(',')).join(' ')}
                  style={{ '--heat': score / 100 }}
                />
                {score > 0 ? (
                  <text
                    x={zone.polygon.reduce((sum, point) => sum + point[0], 0) / zone.polygon.length}
                    y={zone.polygon.reduce((sum, point) => sum + point[1], 0) / zone.polygon.length}
                  >
                    {zone.zone_id} {Math.round(score)}%
                  </text>
                ) : null}
              </g>
            );
          })}
        </svg>
      </div>
    </section>
  );
}

function StoreSection({ store, elapsed }) {
  const abandonment = `${Math.round((store.metrics?.abandonment_rate || 0) * 100)}%`;
  const conversion = `${Math.round((store.metrics?.conversion_rate || 0) * 100)}%`;
  return (
    <section className="store-section">
      <header className="store-header">
        <div>
          <span className="store-code">{store.store_id}</span>
          <h2>{store.store_name}</h2>
        </div>
        <div className="store-metrics">
          <span><Users size={15} /><strong>{store.metrics?.unique_visitors || 0}</strong> visitors</span>
          <span><TrendingUp size={15} /><strong>{conversion}</strong> conversion</span>
          <span><Activity size={15} /><strong>{store.metrics?.queue_depth || 0}</strong> queue</span>
          <span><Clock3 size={15} /><strong>{abandonment}</strong> abandon</span>
          <span><Video size={15} /><strong>{store.cameras.length}</strong> cameras</span>
        </div>
      </header>
      <div className="store-workspace">
        <div className="camera-grid">
          {store.cameras.map((camera) => <VideoFeed camera={camera} elapsed={elapsed} key={camera.camera_id} />)}
        </div>
        <LayoutHeatmap store={store} />
      </div>
    </section>
  );
}

function App() {
  const [snapshot, setSnapshot] = useState(null);
  const [status, setStatus] = useState('loading');
  const [speed, setSpeed] = useState(1);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const data = await fetchJson(`/replay/snapshot?speed=${speed}`);
        if (cancelled) return;
        setSnapshot(data);
        setStatus(data.status === 'LIVE' ? 'live' : 'offline');
      } catch (error) {
        if (!cancelled) {
          console.error(error);
          setStatus('offline');
        }
      }
    }
    load();
    const interval = window.setInterval(load, 1000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [speed]);

  return (
    <main className="shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Purplle Store Intelligence</p>
          <h1>Live Multi-Store Command View</h1>
          <p className="subtle">All recordings synchronized to one replay timeline</p>
        </div>
        <div className="live-controls">
          <div className={`status ${status}`}><span />{status}</div>
          <div className="timeline">
            <strong>{formatClock(snapshot?.elapsed_seconds)}</strong>
            <span>/ {formatClock(snapshot?.duration_seconds)}</span>
          </div>
          <div className="segmented" aria-label="Replay speed">
            {[1, 2, 5].map((value) => (
              <button className={speed === value ? 'active' : ''} key={value} onClick={() => setSpeed(value)} type="button">
                {value}x
              </button>
            ))}
          </div>
        </div>
      </header>

      <div className="replay-progress">
        <span style={{ width: `${((snapshot?.elapsed_seconds || 0) / Math.max(snapshot?.duration_seconds || 1, 1)) * 100}%` }} />
      </div>

      <div className="stores">
        {(snapshot?.stores || []).map((store) => (
          <StoreSection elapsed={snapshot?.elapsed_seconds || 0} key={store.store_id} store={store} />
        ))}
      </div>

      <footer className="footer">
        <RefreshCcw size={14} />
        Live snapshot updates every second
      </footer>
    </main>
  );
}

createRoot(document.getElementById('root')).render(<App />);
