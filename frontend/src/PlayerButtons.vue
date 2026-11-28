<script setup lang="ts">
import {computed,onMounted,onUnmounted,ref,watch} from 'vue'
import {Play,MonitorPlay,CirclePlay,TrafficCone,Clapperboard,Ellipsis,RefreshCw} from '@lucide/vue'
import {type Task} from './api'
import {primaryVideo} from './catalog'
import {inlinePlayers,players,defaultPlayer,launchPlayer,copy,type Playback,type Player} from './players'
import {preparePlayback,preparedPlayback} from './playback-cache'
const props=defineProps<{task:Task}>()
const emit=defineEmits<{fallback:[playback:Playback];notice:[message:string]}>()
const ready=ref<Playback>(),loading=ref(false),error=ref('')
const file=computed(()=>primaryVideo(props.task)),choices=inlinePlayers()
const icons:Record<string,any>={potplayer:MonitorPlay,iina:CirclePlay,'vlc-ios':TrafficCone,'vlc-android':TrafficCone,vidhub:Clapperboard}
let disposed=false,version=0
function reset(){version++;loading.value=false;ready.value=undefined;error.value=''}
function currentReady(){
 const cached=ready.value||(file.value?preparedPlayback(props.task.id,file.value.pickcode):undefined)
 if(cached&&cached.expires>Date.now()/1000+5){ready.value=cached;return cached}
 ready.value=undefined
 return undefined
}
async function ensureReady(){
 const existing=currentReady()
 if(existing)return existing
 if(!file.value||loading.value)return undefined
 const ticket=++version,task=props.task.id,pick=file.value.pickcode
 loading.value=true;error.value=''
 try{
  const result=await preparePlayback(task,pick)
  if(!disposed&&ticket===version){ready.value=result;return result}
 }catch(e){
  const msg=(e as Error).message||'准备播放失败'
  if(!disposed&&ticket===version){error.value=msg;emit('notice',msg)}
 }finally{
  if(ticket===version)loading.value=false
 }
 return undefined
}
async function launch(player?:Player){
 let preferred=defaultPlayer();try{preferred=localStorage.getItem('ltx.player')||preferred}catch{}
 const selected=player||players.find(p=>p.id===preferred)||players[players.length-1]
 const playback=await ensureReady()
 if(!playback)return
 try{
  launchPlayer(selected,playback)
  emit('notice','已请求打开 '+selected.name+'；没有响应时可使用旁边的更多播放选项')
 }catch(e){
  if(selected.clipboard){
   try{
    await copy(playback.url)
    try{localStorage.setItem('ltx.player',selected.id)}catch{}
    window.location.href=selected.build(playback)
    emit('notice','已复制地址并请求打开 '+selected.name+'；没有响应时可使用旁边的更多播放选项')
    return
   }catch{}
  }
  emit('notice',(e as Error).message)
  emit('fallback',playback)
 }
}
async function openMore(){
 const playback=await ensureReady()
 if(playback)emit('fallback',playback)
}
watch(()=>props.task.id+':'+file.value?.pickcode,()=>{version++;loading.value=false;ready.value=undefined;error.value=''})
onMounted(()=>{window.addEventListener('playback-reset',reset)})
onUnmounted(()=>{disposed=true;version++;window.removeEventListener('playback-reset',reset)})
</script>
<template><div class="inline-playback">
 <div class="player-row" role="group" :aria-label="task.resource+' 播放器'" :aria-busy="loading">
  <button class="play-button ready" :disabled="!file||loading" @click="launch()" :title="loading?'正在准备播放地址':'点击获取地址并用上次选择的播放器打开'"><Play :size="13"/>{{loading?'准备中':'播放'}}</button>
  <button v-for="p in choices" :key="p.id" class="player-icon" :class="p.id" :disabled="!file||loading" :title="'用 '+p.name+' 播放'" :aria-label="'用 '+p.name+' 播放'" @click="launch(p)"><component :is="icons[p.id]" :size="17"/></button>
  <button class="player-icon player-more" :disabled="!file||loading" title="复制播放地址 / M3U" aria-label="更多播放选项" @click="openMore"><Ellipsis :size="16"/></button>
 </div>
 <button v-if="error" class="playback-retry" :title="error" @click="launch()"><RefreshCw :size="12"/>准备失败，点击重试</button>
</div></template>
