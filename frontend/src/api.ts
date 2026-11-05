export let csrf = ''
export function setCSRF(value:string) { csrf=value }
export class APIError extends Error { constructor(message:string, public status:number){super(message)} }
export async function api<T=any>(url:string, method='GET', body?:unknown):Promise<T> {
  const response=await fetch('/api'+url,{method,credentials:'same-origin',headers:{'Content-Type':'application/json',...(csrf?{'X-CSRF-Token':csrf}:{})},body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(45000)})
  const data=await response.json().catch(()=>({detail:'服务器返回了无法识别的响应'}))
  if(!response.ok) {
    if(response.status===401 && !url.startsWith('/auth/login')) window.dispatchEvent(new Event('session-expired'))
    throw new APIError(typeof data.detail==='string'?data.detail:'请求参数不正确',response.status)
  }
  return data as T
}
export const time=(value:number|null|undefined)=>value?new Date(value*1000).toLocaleString('zh-CN',{hour12:false}):'尚无记录'
export const safeURL=(url:string|undefined)=>url && /^https?:\/\//i.test(url)?url:undefined
export interface Item {code:string;title:string;title_zh?:string;actors:string[];maker?:string;date?:string;cover?:string;shot?:string;url?:string;score?:number;links:string[];pending?:boolean;added_at?:number;genres?:string[]}
export interface Media {pickcode:string;name:string;size:number}
export interface Task {id:string;resource:string;title:string;state:string;percent:number;error:string;created:number;updated:number;files:Media[]}
export const stateNames:Record<string,string>={submitting:'提交中',unknown:'结果待确认',downloading:'离线下载中',completed:'可播放',failed:'失败',missing:'文件失效',cancelled:'已取消'}
