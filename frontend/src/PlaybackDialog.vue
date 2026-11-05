<script setup lang="ts">
import {ref,computed} from 'vue'
import {players,platform,defaultPlayer,copy,type Playback} from './players'
const props=defineProps<{playback:Playback}>()
const emit=defineEmits<{close:[]}>()
const chosen=ref(localStorage.getItem('ltx.player')||defaultPlayer())
const selected=computed(()=>players.find(p=>p.id===chosen.value)||players[players.length-1])
const available=players.filter(p=>p.platforms.includes(platform()))
const copied=ref(false),message=ref('')
function choose(){localStorage.setItem('ltx.player',chosen.value);copied.value=false;message.value=''}
async function copyForPot(){try{await copy(props.playback.url);copied.value=true;message.value='地址已复制。现在点击打开 PotPlayer。'}catch(e){message.value=(e as Error).message}}
function launch(){
 if(Date.now()/1000>=props.playback.expires){message.value='播放地址已过期，请关闭后重新准备播放';return}
 if(selected.value.clipboard&&!copied.value){message.value='请先复制播放地址，再打开 PotPlayer';return}
 window.location.href=selected.value.build(props.playback)
 message.value='已请求打开播放器。若没有响应，请检查是否已安装、允许浏览器打开应用，或复制地址到播放器。'
}
async function copyURL(){try{await copy(props.playback.url);message.value='播放地址已复制'}catch(e){message.value=(e as Error).message}}
</script>
<template><div class="overlay" @click.self="emit('close')"><section class="dialog" role="dialog" aria-modal="true" aria-labelledby="play-title">
 <button class="close" @click="emit('close')" aria-label="关闭">×</button><span class="eyebrow">READY TO PLAY</span><h2 id="play-title">在你的播放器中打开</h2><p class="muted filename">{{playback.name}}</p>
 <label>播放器<select v-model="chosen" @change="choose"><option v-for="p in available" :key="p.id" :value="p.id">{{p.name}}</option><optgroup label="其他设备"><option v-for="p in players.filter(p=>!available.includes(p))" :key="p.id" :value="p.id">{{p.name}}</option></optgroup></select></label>
 <p class="hint">视频直接从 115 读取，本站不转发视频。</p>
 <div class="actions"><button v-if="selected.clipboard" @click="copyForPot">① 复制播放地址</button><button class="primary" @click="launch" :disabled="selected.clipboard&&!copied">{{selected.clipboard?'② ':''}}打开 {{selected.name}}</button></div>
 <p v-if="message" class="notice" role="status">{{message}}</p><div class="actions"><button @click="copyURL">复制地址</button><a class="button" :href="playback.m3u_url">下载 M3U</a></div>
 <details><summary>手动复制 / 打开说明</summary><textarea readonly :value="playback.url" aria-label="播放地址"></textarea><p class="hint">PotPlayer 使用剪贴板启动，需要先复制再点击打开；不会等待网络后自动拉起。地址仅允许播放当前文件，有效期12小时。</p></details>
 </section></div></template>
