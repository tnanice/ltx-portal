<script setup lang="ts">
import {computed,onMounted,onUnmounted,ref,watch} from 'vue'
import {Play,MonitorPlay,CirclePlay,TrafficCone,Clapperboard,Ellipsis,RefreshCw} from '@lucide/vue'
import {type Task} from './api'
import {primaryVideo} from './catalog'
import {inlinePlayers,players,defaultPlayer,launchPlayer,type Playback,type Player} from './players'
import {preparePlayback,preparedPlayback} from './playback-cache'
const props=defineProps<{task:Task}>()
const emit=defineEmits<{fallback:[playback:Playback];notice:[message:string]}>()
const root=ref<HTMLElement>(),ready=ref<Playback>(),loading=ref(false),error=ref('')
const file=computed(()=>primaryVideo(props.task)),choices=inlinePlayers()
const icons:Record<string,any>={potplayer:MonitorPlay,iina:CirclePlay,'vlc-ios':TrafficCone,'vlc-android':TrafficCone,vidhub:Clapperboard}
let observer:IntersectionObserver|undefined,visible=false,disposed=false,version=0,timer:ReturnType<typeof setInterval>|undefined
function reset(){version++;loading.value=false;ready.value=undefined;error.value=''}
async function prepare(){
 if(!file.value||loading.value)return
 const ticket=++version,task=props.task.id,pick=file.value.pickcode
 loading.value=true;error.value=''
 try{const result=await preparePlayback(task,pick);if(!disposed&&ticket===version)ready.value=result}
 catch(e){if(!disposed&&ticket===version)error.value=(e as Error).message}
 finally{if(ticket===version)loading.value=false}
}
function launch(player?:Player){
 if(!ready.value||ready.value.expires<=Date.now()/1000+5){ready.value=undefined;void prepare();emit('notice','正在刷新播放地址，图标亮起后即可打开');return}
 let preferred=defaultPlayer();try{preferred=localStorage.getItem('ltx.player')||preferred}catch{}
 const selected=player||players.find(p=>p.id===preferred)||players[players.length-1]
 try{launchPlayer(selected,ready.value);emit('notice','已请求打开 '+selected.name+'；没有响应时可使用旁边的更多播放选项')}
 catch(e){emit('notice',(e as Error).message);emit('fallback',ready.value)}
}
watch(()=>props.task.id+':'+file.value?.pickcode,()=>{version++;loading.value=false;ready.value=undefined;if(visible)void prepare()})
onMounted(()=>{
 window.addEventListener('playback-reset',reset)
 observer=new IntersectionObserver(entries=>{visible=entries.some(e=>e.isIntersecting);if(visible&&!ready.value&&!error.value)void prepare()},{rootMargin:'120px'})
 if(root.value)observer.observe(root.value)
 timer=setInterval(()=>{if(visible&&file.value&&!preparedPlayback(props.task.id,file.value.pickcode)&&!error.value){ready.value=undefined;void prepare()}},30000)
})
onUnmounted(()=>{disposed=true;version++;observer?.disconnect();clearInterval(timer);window.removeEventListener('playback-reset',reset)})
</script>
<template><div ref="root" class="inline-playback">
 <div class="player-row" role="group" :aria-label="task.resource+' 播放器'" :aria-busy="loading">
  <button class="play-button ready" :disabled="!ready" @click="launch()" :title="loading?'正在准备播放地址':'用上次选择的播放器打开'"><Play :size="13"/>{{loading?'准备中':'播放'}}</button>
  <button v-for="p in choices" :key="p.id" class="player-icon" :class="p.id" :disabled="!ready" :title="'用 '+p.name+' 播放'" :aria-label="'用 '+p.name+' 播放'" @click="launch(p)"><component :is="icons[p.id]" :size="17"/></button>
  <button class="player-icon player-more" :disabled="!ready" title="复制播放地址 / M3U" aria-label="更多播放选项" @click="emit('fallback',ready!)"><Ellipsis :size="16"/></button>
 </div>
 <button v-if="error" class="playback-retry" :title="error" @click="prepare"><RefreshCw :size="12"/>准备失败，点击重试</button>
</div></template>
