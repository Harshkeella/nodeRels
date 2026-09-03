// One stroke weight, one cap style, one 22-unit grid. Inline paths rather than an icon
// package: two dozen glyphs is not worth a dependency, a build step and a tree-shake.
const P = {
  clip: 'M12.5 6.5 7 12a2.5 2.5 0 0 0 3.5 3.5l5.5-5.5a4 4 0 0 0-5.7-5.7l-5.5 5.6a5.5 5.5 0 0 0 7.8 7.8l4.9-4.9',
  x: 'M6 6l10 10M16 6L6 16',
  dl: 'M11 3.5v10m0 0 3.6-3.6M11 13.5 7.4 9.9M3.8 15v2.2a1.3 1.3 0 0 0 1.3 1.3h11.8a1.3 1.3 0 0 0 1.3-1.3V15',
  trash: 'M4.5 6h13M9 6V4.6A1.1 1.1 0 0 1 10.1 3.5h1.8A1.1 1.1 0 0 1 13 4.6V6m3 0-.6 11a1.2 1.2 0 0 1-1.2 1.1H8.8A1.2 1.2 0 0 1 7.6 17L7 6',
  alert: 'M9 5.4v4.2M9 12.4v.2',
  left: 'M12 5l-6 6 6 6',
  right: 'M8 5l6 6-6 6',
  play: 'M7.5 4.8 16 11l-8.5 6.2z',
  notes: 'M5 4.5h12M5 9h12M5 13.5h7',
  plus: 'M11 5v12M5 11h12',
  minus: 'M5 11h12',
  more: 'M6 11h.01M11 11h.01M16 11h.01',
  undo: 'M6.5 8.5H13a4.5 4.5 0 1 1 0 9h-6M6.5 8.5l3-3m-3 3 3 3',
  redo: 'M15.5 8.5H9a4.5 4.5 0 1 0 0 9h6m.5-9-3-3m3 3-3 3',
  copy: 'M8 8.5h8.2a1 1 0 0 1 1 1V18a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1V9.5a1 1 0 0 1 1-1ZM4.8 14.5H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h8.2a1 1 0 0 1 1 1v.8',
  eye: 'M2.5 11S5.6 5.5 11 5.5 19.5 11 19.5 11 16.4 16.5 11 16.5 2.5 11 2.5 11Z',
  hidden: 'M4 4l14 14M8.6 8.7A3 3 0 0 0 11 14a3 3 0 0 0 2.4-1.2M6.3 6.5C3.9 8 2.5 11 2.5 11s3.1 5.5 8.5 5.5c1.5 0 2.8-.4 3.9-1M16 13.6c2-1.4 3.5-2.6 3.5-2.6S16.4 5.5 11 5.5c-.6 0-1.1 0-1.6.2',
  lock: 'M6.5 10V7.6a4.5 4.5 0 0 1 9 0V10M5.6 10h10.8a1 1 0 0 1 1 1v6.2a1 1 0 0 1-1 1H5.6a1 1 0 0 1-1-1V11a1 1 0 0 1 1-1Z',
  unlock: 'M6.5 10V7.6a4.5 4.5 0 0 1 8.7-1.6M5.6 10h10.8a1 1 0 0 1 1 1v6.2a1 1 0 0 1-1 1H5.6a1 1 0 0 1-1-1V11a1 1 0 0 1 1-1Z',
  grid: 'M3.5 3.5h6v6h-6zM12.5 3.5h6v6h-6zM3.5 12.5h6v6h-6zM12.5 12.5h6v6h-6z',
  type: 'M4 5.5h14M11 5.5V18M8 18h6',
  shape: 'M4.5 4.5h13v13h-13z',
  image: 'M3.5 4.5h15v13h-15zM3.5 14l4.2-4 3.5 3.3 3-2.8 4.3 4',
  chart: 'M4 18V9.5M9.3 18V4.5M14.7 18v-6M20 18V7',
  table: 'M3.5 4.5h15v13h-15zM3.5 9h15M8.5 9v8.5M13.5 9v8.5',
  spark: 'M11 3.5l1.8 4.7 4.7 1.8-4.7 1.8L11 16.5l-1.8-4.7L4.5 10l4.7-1.8zM17 15.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z',
  history: 'M3.5 11a7.5 7.5 0 1 0 2.2-5.3M3.5 5v3.5H7M11 7v4.3l3 1.8',
}

const DOTS = { more: [[6, 11], [11, 11], [16, 11]] }
const FILLED = new Set(['play', 'spark'])

export default function Icon({ name, size = 20 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 22 22" fill="none" stroke="currentColor"
         strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {name === 'alert' && <circle cx="9" cy="9" r="7" />}
      {name === 'eye' && <circle cx="11" cy="11" r="2.6" />}
      {DOTS[name]?.map(([cx, cy]) => <circle key={cx} cx={cx} cy={cy} r=".9" fill="currentColor" stroke="none" />)}
      {!DOTS[name] && <path d={P[name]} fill={FILLED.has(name) ? 'currentColor' : 'none'} />}
    </svg>
  )
}

export function IconButton({ icon, label, onClick, className = '', disabled }) {
  return (
    <button className={'icon-btn ' + className} title={label} aria-label={label}
            disabled={disabled}
            onClick={e => { e.stopPropagation(); onClick() }}>
      <Icon name={icon} />
    </button>
  )
}
