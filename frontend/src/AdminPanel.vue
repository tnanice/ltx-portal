<script setup lang="ts">
import {ref,onMounted} from 'vue'
import {api,time} from './api'
const emit=defineEmits<{changed:[]}>()
const users=ref<any[]>([]),status=ref<any>({sync:{}}),username=ref(''),password=ref(''),minutes=ref(60),enabled=ref(true),message=ref(''),busy=ref(false),logs=ref<any[]>([])
const triggerNames:Record<string,string>={interval:'定时',manual:'手动',relay:'推送即拉'}
async function load(){const [u,s,l]=await Promise.all([api('/admin/users'),api('/catalog/status'),api('/admin/sync-log')]);users.value=u;status.value=s;logs.value=l.items;minutes.value=s.sync.interval_seconds/60;enabled.value=s.sync.enabled}
async function action(fn:()=>Promise<void>){busy.value=true;message.value='';try{await fn();emit('changed')}catch(e){message.value=(e as Error).message}finally{busy.value=false}}
function create(){action(async()=>{await api('/admin/users','POST',{username:username.value,password:password.value});username.value='';password.value='';await load();message.value='账号已创建，请将初始密码交给使用者'})}
function toggle(u:any){if(!confirm((u.disabled?'启用':'禁用')+'账号 '+u.username+'？'))return;action(async()=>{await api('/admin/users/'+u.id,'PATCH',{disabled:!u.disabled});await load()})}
function syncNow(){
  action(async()=>{
    await api('/admin/sync-now','POST')
    await load()
    setTimeout(()=>{void load()},8000)
  })
}
function reset(u:any){const value=prompt('为 '+u.username+' 设置新的密码（至少12位）。所有已登录设备将退出。');if(!value)return;action(async()=>{await api('/admin/users/'+u.id,'PATCH',{password:value});message.value='密码已重置，原会话和播放凭证已撤销'})}
onMounted(()=>action(load))
</script>
<template><div class="admin-grid"><section class="panel"><span class="eyebrow">SYNC CONTROL</span><h2>同步设置</h2><p class="muted">同步源站数据，异常时保留最后一份正常快照。</p>
 <form @submit.prevent="action(async()=>{await api('/admin/sync','PUT',{enabled,interval_seconds:Math.round(minutes*60)});await load();message='同步设置已保存'})"><label class="check"><input type="checkbox" v-model="enabled">启用自动同步</label><label>同步间隔（分钟）<input type="number" v-model="minutes" min="5" max="10080" required></label><div class="actions"><button class="primary" :disabled="busy">保存设置</button><button type="button" :disabled="busy" @click="syncNow">立即同步</button></div></form>
 <dl class="facts"><dt>最后成功</dt><dd>{{time(status.sync.last_success)}}</dd><dt>下次同步</dt><dd>{{status.sync.enabled?time(status.sync.next_sync):'已暂停'}}</dd><dt>资源数量</dt><dd>{{status.sync.count||0}}</dd></dl><p v-if="status.sync.last_error" class="notice">{{status.sync.last_error}}</p>
 <h3 class="log-title">最近同步</h3><ul class="sync-log"><li v-for="row in logs" :key="row.id" :class="{fail:!row.ok}"><time>{{time(row.ts)}}</time><span class="tag">{{triggerNames[row.trigger]||row.trigger}}</span><span class="result">{{row.ok?(row.detail==='304'?'未变更':(row.count??'—')+' 条'):'失败'}}</span><span class="muted">{{(row.duration_ms/1000).toFixed(1)}}s</span><small v-if="row.error" class="err">{{row.error}}</small></li><li v-if="!logs.length" class="muted">暂无记录，同步一次后在此显示</li></ul></section>
 <section class="panel"><span class="eyebrow">MEMBERS</span><h2>账号管理</h2><p class="muted">关闭公开注册，每位成员自行绑定自己的115。</p><form @submit.prevent="create" class="new-user"><label>新账号<input v-model="username" required maxlength="64" autocomplete="off"></label><label>初始密码<input type="password" v-model="password" required minlength="12" maxlength="256" autocomplete="new-password" placeholder="至少12位"></label><button class="primary" :disabled="busy">创建账号</button></form>
 <div class="member" v-for="u in users" :key="u.id"><div><strong>{{u.username}}</strong><small>{{u.role==='admin'?'管理员':'普通用户'}} · {{u.disabled?'已禁用':'正常'}}</small></div><div class="actions"><button :disabled="busy" @click="reset(u)">重置密码</button><button v-if="u.role!=='admin'" :disabled="busy" @click="toggle(u)">{{u.disabled?'启用':'禁用'}}</button></div></div></section><p v-if="message" class="notice" role="status">{{message}}</p></div></template>
