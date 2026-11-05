import type {Task} from './api'

export function preferredTask(tasks:Task[], code:string){
 const rank:Record<string,number>={completed:0,downloading:1,submitting:2,unknown:3,missing:4,failed:5,cancelled:6}
 return tasks.filter(t=>t.resource===code).sort((a,b)=>(rank[a.state]??9)-(rank[b.state]??9)||b.updated-a.updated)[0]
}

export function primaryVideo(task:Task){
 const normal=(s:string)=>s.toUpperCase().replace(/[^A-Z0-9]/g,'')
 const code=normal(task.resource)
 const score=(name:string)=>Number(Boolean(code)&&normal(name).includes(code))*2-Number(/sample|trailer|preview|预告|样片/i.test(name))*4
 return [...task.files].sort((a,b)=>score(b.name)-score(a.name)||(Number(b.size)||0)-(Number(a.size)||0)||a.name.localeCompare(b.name,undefined,{numeric:true}))[0]
}

export function copyLinkLabel(links:string[]){
 const ed2k=links.find(l=>l.startsWith('ed2k:'))
 if(!ed2k)return links.length?'复制磁力链接':'暂无链接'
 const size=Number(ed2k.split('|')[3])
 return '复制 ed2k'+(size>0?' · '+(size/1024**3).toFixed(1)+'G':'')
}
