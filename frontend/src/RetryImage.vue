<script setup lang="ts">
import {ref,computed,watch,onUnmounted} from 'vue'
const props=withDefaults(defineProps<{src:string;alt:string;loading?:'lazy'|'eager';contain?:boolean}>(),{loading:'eager',contain:false})
const attempt=ref(0),failed=ref(false)
let timer:ReturnType<typeof setTimeout>|undefined
const source=computed(()=>{
 if(!attempt.value)return props.src
 const url=new URL(props.src)
 // Preserve signed/query URLs; static paths can bypass cached failure responses.
 if(!url.search)url.searchParams.set('_ltx_retry',String(attempt.value))
 return url.href
})
function retry(){
 if(timer)return
 if(attempt.value>=2){failed.value=true;return}
 timer=setTimeout(()=>{timer=undefined;attempt.value++;failed.value=false},attempt.value===0?1000:3000)
}
watch(()=>props.src,()=>{clearTimeout(timer);timer=undefined;attempt.value=0;failed.value=false})
onUnmounted(()=>clearTimeout(timer))
</script>
<template>
 <span class="retry-image" :class="{contain}">
  <img v-if="!failed" :key="attempt" :src="source" :alt="alt" :loading="loading" referrerpolicy="no-referrer" @error="retry">
  <span v-else class="image-unavailable" role="img" :aria-label="alt+' 暂时无法加载'">图片暂时无法加载</span>
 </span>
</template>
<style scoped>
.retry-image{display:block;width:100%;height:100%}.retry-image>img{display:block;width:100%;height:100%;object-fit:cover}.retry-image.contain{height:auto}.retry-image.contain>img{height:auto;max-height:420px;object-fit:contain}.image-unavailable{display:flex;align-items:center;justify-content:center;width:100%;height:100%;min-height:80px;padding:12px;text-align:center;font-size:12px;color:#a0a5af;background:#171a21}
</style>
