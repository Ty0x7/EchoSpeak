import React from "react";

/* Editorial illustrations. Measured values are identified in the adjoining text. */
export function EchoBlueprint() {
  return <svg aria-hidden="true" viewBox="0 0 390 215" className="r-echo-art" fill="none">
    <defs>
      <pattern id="echo-blueprint-grid" width="15" height="15" patternUnits="userSpaceOnUse">
        <path d="M15 0H0v15" stroke="currentColor" opacity=".16" />
      </pattern>
      <clipPath id="echo-blueprint-face"><rect x="137" y="46" width="116" height="116" rx="27" /></clipPath>
    </defs>
    <rect x="29" y="14" width="332" height="180" rx="3" fill="url(#echo-blueprint-grid)" />
    <g stroke="currentColor" opacity=".42">
      <path d="M29 31V14h17m298 0h17v17M29 177v17h17m298 0h17v-17M195 23v162m-133-81h266" strokeDasharray="4 4" />
      <path d="M137 35h116m-116-5v10m116-10v10m14 6v116m-5-116h10m-10 116h10M169 95l-57-37H63m158 45 71 37h35" />
      <circle cx="63" cy="58" r="3" /><circle cx="327" cy="140" r="3" />
      <rect x="126" y="35" width="138" height="138" rx="34" strokeDasharray="5 6" />
    </g>
    <rect x="137" y="46" width="116" height="116" rx="27" stroke="currentColor" strokeWidth="2" className="r-board-face" />
    <g clipPath="url(#echo-blueprint-face)">
      <path className="r-blueprint-scan" d="M137 46h116v28H137z" fill="currentColor" opacity=".18" />
      <g className="r-blueprint-eyes" fill="currentColor">
        <rect x="164" y="88" width="13" height="31" rx="6.5" />
        <rect x="213" y="88" width="13" height="31" rx="6.5" />
      </g>
    </g>
    <g stroke="currentColor" strokeDasharray="3 5" opacity=".22">
      <path d="M118 173h154M118 35v138m154-138v138" />
    </g>
  </svg>;
}

function StudyDrawing({ slug }: { slug: string }) {
  if (slug === "accessible-hardware") return (
    <svg aria-hidden="true" viewBox="0 0 320 130" stroke="currentColor" fill="none" strokeWidth="1.6">
      <g transform="translate(77 65) scale(1 .52) rotate(-45)">
        {[-14, 0, 14].map(offset => <rect key={offset} x={offset} y={offset} width="115" height="65" rx="5" className="r-board-face" />)}
        <rect x="33" y="29" width="76" height="34" rx="2" />
        {Array.from({length: 11}, (_, i) => <path key={i} d={`M${27 + 8 * i} 13V7m0 73v7`} />)}
      </g>
      <path d="M180 42l14-12h43m-71 47 17 18h64" strokeDasharray="3 3" />
      <circle cx="240" cy="30" r="3" /><circle cx="250" cy="95" r="3" />
    </svg>
  );
  if (slug === "same-model-better-harness") return (
    <svg aria-hidden="true" viewBox="0 0 320 130">
      <g stroke="currentColor" fill="none">
        {[70, 110, 150, 190, 230, 270].map(x => <path key={x} d={`M${x} 22v85`} opacity=".18" strokeDasharray="2 4" />)}
        <path d="M70 35h96v18H70z" fill="currentColor" opacity=".32" />
        <path d="M70 70h166v18H70z" fill="currentColor" />
      </g>
      <g fill="currentColor" fontSize="13" fontFamily="monospace"><text x="174" y="49">48%</text><text x="244" y="84">83%</text></g>
    </svg>
  );
  if (slug === "bonsai-27b-on-8gb") return (
    <svg aria-hidden="true" viewBox="0 0 320 130">
      {[0, 1, 2].map((layer) => <g key={layer} transform={`translate(${39 + layer * 65} ${44 - layer * 9}) skewY(10)`}>
        <rect x="9" y="7" width="109" height="60" rx="4" className="r-board-side" stroke="currentColor" />
        <rect width="109" height="60" rx="4" className="r-board-face" stroke="currentColor" />
        <text x="52" y="40" fontSize="30" textAnchor="middle" fill="currentColor" fontFamily="monospace">{["−1", "0", "+1"][layer]}</text>
      </g>)}
    </svg>
  );
  if (slug === "evidence-based-execution") return (
    <svg aria-hidden="true" viewBox="0 0 320 130" stroke="currentColor" fill="none" strokeWidth="2">
      <path d="M86 23h81l17 17v62H86zM166 23v18h18" className="r-board-face" />
      <path d="M101 50h45m-45 12h65m-65 12h46m-46 12h38" opacity=".6" />
      <circle cx="193" cy="68" r="32" className="r-board-face" /><path d="m215 92 22 23" strokeWidth="5" />
      <path d="m180 68 9 9 18-20" strokeWidth="3" />
    </svg>
  );
  return (
    <svg aria-hidden="true" viewBox="0 0 320 130" stroke="currentColor" fill="none" strokeWidth="1.8">
      <path d="M59 65h28q8 0 15-8l14-15q6-7 16-7h31M88 65q7 0 14 8l14 15q6 7 16 7h31m18-60h11q8 0 15 9l9 13q6 8 14 8h19m-68 30h11q8 0 15-9l9-13q6-8 14-8" />
      <rect x="42" y="53" width="24" height="24" rx="4" className="r-board-face" />
      <circle cx="167" cy="35" r="12" className="r-board-face" /><circle cx="167" cy="95" r="12" className="r-board-face" />
      <circle cx="255" cy="65" r="17" className="r-board-face" /><path d="m247 65 6 6 10-12" />
    </svg>
  );
}

export function StudyArt({ slug }: { slug: string }) {
  return <div className="r-study-art" aria-hidden="true"><StudyDrawing slug={slug} /></div>;
}
