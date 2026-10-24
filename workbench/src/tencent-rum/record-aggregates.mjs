// Pure aggregation over privacy-normalized Tencent RUM records; hashes stay in process memory.
const day=value=>new Date(value).toLocaleDateString('en-CA',{timeZone:'Asia/Shanghai'});
export function aggregatePageRecords(records,coverage={}){
 const visitors=new Set(),sessions=new Map(),pages=new Map(),daily=new Map(),bySource=new Map();
 for(const r of records){visitors.add(r.visitorHash);const session=sessions.get(r.sessionHash)||[];session.push(r);sessions.set(r.sessionHash,session);
  const p=pages.get(r.path)||{path:r.path,pv:0,visitors:new Set(),sessions:new Set(),entries:0,exits:0,singlePageSessions:0};p.pv++;p.visitors.add(r.visitorHash);p.sessions.add(r.sessionHash);pages.set(r.path,p);
  const date=day(r.timestamp),point=daily.get(date)||{date,pv:0,visitors:new Set(),sessions:new Set()};point.pv++;point.visitors.add(r.visitorHash);point.sessions.add(r.sessionHash);daily.set(date,point);
 }
 for(const rows of sessions.values()){rows.sort((a,b)=>a.timestamp.localeCompare(b.timestamp));const source=rows[0].source||'unknown',channel=bySource.get(source)||{name:source,pv:0,sessions:0,visitors:new Set()};channel.pv+=rows.length;channel.sessions++;rows.forEach(r=>channel.visitors.add(r.visitorHash));bySource.set(source,channel);pages.get(rows[0].path).entries++;pages.get(rows.at(-1).path).exits++;if(new Set(rows.map(r=>r.path)).size===1)pages.get(rows[0].path).singlePageSessions++;}
 if(coverage.complete&&coverage.start&&coverage.end){let t=Date.parse(coverage.start);while(t<Date.parse(coverage.end)){const date=day(t);if(!daily.has(date))daily.set(date,{date,pv:0,visitors:new Set(),sessions:new Set()});t+=86400000;}const last=day(Date.parse(coverage.end)-1);if(!daily.has(last))daily.set(last,{date:last,pv:0,visitors:new Set(),sessions:new Set()});}
 return {pv:records.length,uv:visitors.size,sessions:sessions.size,pages:[...pages.values()].map(p=>({path:p.path,pv:p.pv,uv:p.visitors.size,sessions:p.sessions.size,entries:p.entries,exits:p.exits,singlePageSessions:p.singlePageSessions})).sort((a,b)=>b.pv-a.pv||a.path.localeCompare(b.path)),trend:[...daily.values()].sort((a,b)=>a.date.localeCompare(b.date)).map(p=>({date:p.date,pv:p.pv,uv:p.visitors.size,sessions:p.sessions.size})),sources:[...bySource.values()].map(p=>({name:p.name,pv:p.pv,uv:p.visitors.size,sessions:p.sessions})).sort((a,b)=>b.pv-a.pv),coverage};
}
export function attachEngagement(pages,business){
 const observed=new Map((business?.engagement?.byPath||[]).map(p=>[p.path,p]));
 return pages.map(p=>{const sample=observed.get(p.path);return {...p,avgDurationSeconds:sample?.avgDurationSeconds??null,durationKnownSessions:sample?.sessions??null,durationCoverage:sample&&p.sessions>0&&sample.sessions<=p.sessions?sample.sessions/p.sessions:null};});
}
export function dimensionsFromV2(response,field){
 const counts=new Map();for(const result of response?.Result?.results||[])for(const series of result.series||[])for(const row of series.rows||[]){const name=series.tags?.[field]??row[field];const count=row.allCount??row.count??row.pv; if(typeof name!=='string'||!Number.isFinite(Number(count)))continue;const label=name.trim().slice(0,120)||'未知';counts.set(label,(counts.get(label)||0)+Number(count));}
 return [...counts].map(([name,count])=>({name,count})).sort((a,b)=>b.count-a.count);
}
// Tencent's published platform enum: Android=1, iOS=2, Windows=3, macOS=4, Linux=5, devtools=6, unknown=100.
export function platformDimensions(response){
 const names={'1':'Android','2':'iOS','3':'Windows','4':'macOS','5':'Linux','6':'开发者工具','100':'未知'};
 const rows=dimensionsFromV2(response,'platform'),groups=new Map();
 for(const row of rows){const category=['1','2'].includes(row.name)?'mobile':['3','4','5'].includes(row.name)?'desktop':'unknown';groups.set(category,(groups.get(category)||0)+row.count);}
 return {platforms:rows.map(row=>({...row,name:names[row.name]||'未知（'+row.name+'）'})),devices:[...groups].map(([name,pv])=>({name,pv,uv:null})).sort((a,b)=>b.pv-a.pv)};
}
