export type Platform='windows'|'mac'|'ios'|'android'|'other'
export type Playback={url:string;m3u_url:string;name:string;expires:number}
export interface Player {id:string;name:string;platforms:Platform[];build:(p:Playback)=>string;clipboard?:boolean}
export function platform(ua=navigator.userAgent):Platform {
  if(/Android/i.test(ua))return 'android'
  if(/iPhone|iPad|iPod/i.test(ua)||(/Macintosh/.test(ua)&&navigator.maxTouchPoints>1))return 'ios'
  if(/Windows/i.test(ua))return 'windows'
  if(/Mac/i.test(ua))return 'mac'
  return 'other'
}
// New players implement this interface; no 115 API changes are needed.
export const players:Player[]=[
  {id:'potplayer',name:'PotPlayer',platforms:['windows'],clipboard:true,build:()=> 'potplayer:///clipboard'},
  {id:'iina',name:'IINA',platforms:['mac'],build:p=>'iina://weblink?url='+encodeURIComponent(p.url)},
  {id:'vlc-ios',name:'VLC · iOS',platforms:['ios'],build:p=>'vlc-x-callback://x-callback-url/stream?url='+encodeURIComponent(p.url)},
  {id:'vlc-android',name:'VLC · Android',platforms:['android'],build:p=>{
    const u=new URL(p.url);return `intent://${u.host}${u.pathname}${u.search}#Intent;scheme=${u.protocol.slice(0,-1)};package=org.videolan.vlc;type=video/*;end`
  }},
  {id:'vidhub',name:'VidHub',platforms:['ios','android','mac'],build:p=>'open-vidhub://x-callback-url/play?'+new URLSearchParams({url:p.url,filename:p.name,'x-source':'LTX Portal'}).toString()},
  {id:'m3u',name:'下载 M3U',platforms:['windows','mac','ios','android','other'],build:p=>p.m3u_url}
]
export function defaultPlayer(p=platform()){return ({windows:'potplayer',mac:'iina',ios:'vlc-ios',android:'vlc-android',other:'m3u'})[p]}
export function inlinePlayers(device=platform()){
 return ['potplayer','iina',device==='android'?'vlc-android':'vlc-ios','vidhub'].map(id=>players.find(p=>p.id===id)!)
}
// Keep clipboard copying and protocol navigation inside the same click event.
export function copySync(text:string){
 const input=document.createElement('textarea');input.value=text;input.style.position='fixed';input.style.opacity='0';document.body.append(input);input.select()
 try{return document.execCommand('copy')}catch{return false}finally{input.remove()}
}
export function launchPlayer(player:Player,playback:Playback){
 if(playback.expires<=Date.now()/1000)throw new Error('播放地址已过期，请重新准备')
 if(player.clipboard&&!copySync(playback.url))throw new Error('浏览器禁止复制，请复制地址到 PotPlayer，或下载 M3U 打开')
 try{localStorage.setItem('ltx.player',player.id)}catch{}
 window.location.href=player.build(playback)
}
export async function copy(text:string){
  if(navigator.clipboard&&window.isSecureContext){await navigator.clipboard.writeText(text);return}
  if(!copySync(text))throw new Error('浏览器禁止复制，请手动选择并复制地址')
}
