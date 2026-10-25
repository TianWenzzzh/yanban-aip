// 吉祥物：内联 SVG 小猫头鹰（禁外部大图，P2）
export default function Owl({ size = 64, thinking = false }: { size?: number; thinking?: boolean }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" fill="none" aria-label="研伴吉祥物小猫头鹰">
      {/* 身体 */}
      <ellipse cx="32" cy="38" rx="20" ry="19" fill="#2563eb" />
      <ellipse cx="32" cy="42" rx="13" ry="13" fill="#eff6ff" />
      {/* 耳羽 */}
      <path d="M14 24 L12 10 L24 17 Z" fill="#2563eb" />
      <path d="M50 24 L52 10 L40 17 Z" fill="#2563eb" />
      {/* 眼睛 */}
      <circle cx="24" cy="30" r="8.5" fill="white" />
      <circle cx="40" cy="30" r="8.5" fill="white" />
      <circle cx="24" cy="30" r="4.2" fill="#1e3a8a" />
      <circle cx="40" cy="30" r="4.2" fill="#1e3a8a" />
      <circle cx={thinking ? 25.6 : 25.4} cy={thinking ? 28.4 : 28.6} r="1.5" fill="white" />
      <circle cx={thinking ? 41.6 : 41.4} cy={thinking ? 28.4 : 28.6} r="1.5" fill="white" />
      {/* 喙 */}
      <path d="M32 34 L28.5 39.5 L35.5 39.5 Z" fill="#f59e0b" />
      {/* 翅膀 */}
      <path d="M13 36 Q8 44 14 52 Q17 45 16 38 Z" fill="#1d4ed8" />
      <path d="M51 36 Q56 44 50 52 Q47 45 48 38 Z" fill="#1d4ed8" />
      {/* 肚皮纹理 */}
      <path d="M26 44 h12 M28 48 h8 M30 52 h4" stroke="#bfdbfe" strokeWidth="1.6" strokeLinecap="round" />
      {/* 爪子 */}
      <path d="M25 57 v3 M29 57 v3 M35 57 v3 M39 57 v3" stroke="#f59e0b" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}
