import {api} from './api'
import type {Playback} from './players'
const cache=new Map<string,Playback>(),pending=new Map<string,Promise<Playback>>()
let generation=0,active=0
const queue:Array<()=>void>=[]
async function slot(){if(active>=2)await new Promise<void>(resolve=>queue.push(resolve));else active++}
function release(){const next=queue.shift();if(next)next();else active--}
export function resetPlayback(){generation++;cache.clear();pending.clear();window.dispatchEvent(new Event('playback-reset'))}
export function preparedPlayback(task:string,pick:string){
 const result=cache.get(task+':'+pick)
 return result&&result.expires>Date.now()/1000+60?result:undefined
}
export function preparePlayback(task:string,pick:string){
 const key=task+':'+pick,existing=preparedPlayback(task,pick)
 if(existing)return Promise.resolve(existing)
 const current=pending.get(key);if(current)return current
 const epoch=generation
 const job=(async()=>{
  await slot()
  try{
   if(epoch!==generation)throw new Error('登录或绑定已变化，请重新准备')
   const result=await api<Playback>('/playback/tickets','POST',{task_id:task,pickcode:pick})
   if(epoch!==generation)throw new Error('登录或绑定已变化，请重新准备')
   cache.set(key,result)
   if(cache.size>256)cache.delete(cache.keys().next().value!)
   return result
  }finally{release()}
 })()
 pending.set(key,job)
 void job.finally(()=>{if(pending.get(key)===job)pending.delete(key)}).catch(()=>{})
 return job
}
