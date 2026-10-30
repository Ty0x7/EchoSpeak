import React from "react";
export type SubscriptionUsage = { windows?: {label:string;used_percent:number;resets_at?:number;window_minutes?:number}[]; resets_at?:number; observed_at?:number; source?:string; unavailable?:string; status?:string; refresh_error?: string };
export function latestUsage(a?: SubscriptionUsage, b?: SubscriptionUsage): SubscriptionUsage | undefined {
  return (b?.observed_at || 0) > (a?.observed_at || 0) ? b : a || b;
}
export function RuntimeUsage({usage,compact=false}:{usage?:SubscriptionUsage;compact?:boolean}) {
  const rows=usage?.windows || [];
  return <div className={`ec-plan-usage${compact?" is-compact":""}`} aria-label="Subscription usage">
    {usage?.status === "rejected" ? <strong>Runtime reported a subscription limit</strong> : null}
    {usage?.refresh_error ? <small role="status">{usage.refresh_error}</small> : null}
    {rows.length ? rows.map((w,i)=><div key={i}><span>{w.label}</span><strong>{w.used_percent}% used</strong><meter min="0" max="100" value={w.used_percent} aria-label={`${w.label} used percent`}/>{w.resets_at?<small>Resets {new Date(w.resets_at*1000).toLocaleString()}</small>:null}</div>) : <small>{usage?.unavailable || "Subscription allowance unavailable from this runtime."}</small>}
    {!rows.length && usage?.resets_at ? <small>Resets {new Date(usage.resets_at*1000).toLocaleString()}</small> : null}
    {!compact && usage?.observed_at ? <small>Observed {new Date(usage.observed_at*1000).toLocaleString()} · {usage.source}</small> : null}
  </div>;
}
