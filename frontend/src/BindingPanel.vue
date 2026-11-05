<script setup lang="ts">
import {ref,onMounted,onUnmounted} from 'vue'
import {api,time} from './api'
const emit=defineEmits<{changed:[]}>()
const binding=ref<any>({state:'unbound'}),qr=ref<any>(null),app=ref('os_windows'),cookie=ref(''),message=ref(''),busy=ref(false),path=ref('')
let timer:ReturnType<typeof setTimeout>|undefined,disposed=false
const labels:Record<string,string>={unbound:'未绑定',ok:'连接正常',invalid:'登录已失效',unreachable:'暂时不可达',waiting:'用115 App扫描二维码',scanned:'已扫码，请在手机上确认',verifying:'正在验证账号',success:'绑定成功',expired:'二维码已过期',cancelled:'已取消',failed:'验证失败'}
async function load(){binding.value=await api('/me/115');path.value=binding.value.save_path||''}
async function action(fn:()=>Promise<void>){busy.value=true;message.value='';try{await fn()}catch(e){message.value=(e as Error).message}finally{busy.value=false}}
async function poll(){if(!qr.value||disposed)return;try{const r=await api('/me/115/qrcode/'+qr.value.id);if(disposed)return;qr.value={...qr.value,...r};if(r.state==='success'){await load();emit('changed');return}if(['expired','cancelled','failed'].includes(r.state))return}catch(e){message.value=(e as Error).message;return}timer=setTimeout(poll,2000)}
function newQR(){action(async()=>{clearTimeout(timer);qr.value=await api('/me/115/qrcode','POST',{app:app.value});poll()})}
function saveCookie(){action(async()=>{await api('/me/115/cookie','POST',{cookie:cookie.value});cookie.value='';qr.value=null;clearTimeout(timer);await load();emit('changed');message.value='115绑定成功'})}
function unbind(){if(!confirm('解除当前115绑定？原有播放地址将失效，不会删除网盘文件。'))return;action(async()=>{await api('/me/115','DELETE');qr.value=null;clearTimeout(timer);await load();emit('changed')})}
onMounted(()=>action(load));onUnmounted(()=>{disposed=true;clearTimeout(timer);if(qr.value&&!['success','expired','failed','cancelled'].includes(qr.value.state))api('/me/115/qrcode/'+qr.value.id,'DELETE').catch(()=>{})})
</script>
<template><section class="panel binding"><div class="section-head"><div><span class="eyebrow">YOUR CLOUD</span><h2>我的 115</h2><p class="muted">独立绑定你的账号，任务与其他用户隔离。</p></div><span class="pill" :class="binding.state==='ok'?'green':''">{{labels[binding.state]}}</span></div>
 <div v-if="binding.account_id" class="account-info"><strong>账号 {{binding.account_id}}</strong><span class="muted">上次验证 {{time(binding.checked)}}</span><div class="actions"><button :disabled="busy" @click="action(async()=>{await api('/me/115/check','POST');await load()})">检查登录状态</button><button class="danger" :disabled="busy" @click="unbind">解除绑定</button></div></div>
 <div class="binding-grid"><div><h3>{{binding.account_id?'重新扫码绑定':'扫码登录'}}</h3><label>登录设备<select v-model="app"><option value="os_windows">115 Windows 端</option><option value="os_mac">115 macOS 端</option><option value="os_linux">115 Linux 端</option><option value="web">网页版</option><option value="alipaymini">支付宝小程序</option></select></label><p class="hint">选择登录设备后，用手机115 App扫码并确认。</p><button class="primary" :disabled="busy" @click="newQR">{{busy?'请稍候…':qr?'刷新二维码':'生成登录二维码'}}</button>
 <div class="qr-box" v-if="qr"><img :src="qr.image" alt="115登录二维码"><strong>{{labels[qr.state]||qr.state}}</strong><p class="hint">{{qr.error||'请勿将登录二维码分享给他人'}}</p></div></div>
 <div><details><summary>扫码不可用？使用 Cookie 登录</summary><p class="hint">仅发送到你自己的服务器；验证通过后才替换原绑定。</p><textarea v-model="cookie" placeholder="UID=…; CID=…; SEID=…; KID=…" aria-label="115 Cookie" autocomplete="off"></textarea><button :disabled="busy||!cookie" @click="saveCookie">验证并保存</button></details>
 <div v-if="binding.account_id" class="save-path"><h3>离线保存位置</h3><label>115文件夹ID<input v-model="path" placeholder="留空使用115默认位置" inputmode="numeric"></label><button :disabled="busy" @click="action(async()=>{await api('/me/115/settings','PUT',{save_path:path});message='保存位置已更新'})">保存位置</button></div></div></div>
 <p v-if="message" class="notice" role="status">{{message}}</p></section></template>
