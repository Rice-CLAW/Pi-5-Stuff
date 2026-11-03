'use strict';
const SERVICE='802b1000-6a92-4a6f-9b2b-769c88951a01', COMMAND='802b1001-6a92-4a6f-9b2b-769c88951a01', STATUS='802b1002-6a92-4a6f-9b2b-769c88951a01';
const $=id=>document.getElementById(id), decoder=new TextDecoder();
let device,command,status,pendingPing=null,lastSeen=0;
function log(s){$('log').textContent=(new Date().toLocaleTimeString()+'  '+s+'\n'+$('log').textContent).slice(0,6000);}
function reset(){command=null;status=null;clearTimeout(pendingPing);pendingPing=null;$('connect').disabled=!navigator.bluetooth;$('disconnect').disabled=true;$('ping').disabled=true;$('connection').textContent='Disconnected';$('reply').textContent='Disconnected. Reconnect to send commands.';lastSeen=0;$('counter').textContent='—';}
function received(event){const msg=decoder.decode(event.target.value);lastSeen=Date.now();if(msg.startsWith('T:'))$('counter').textContent=msg.slice(2);else {log('Pi → '+msg);if(msg==='PONG'){clearTimeout(pendingPing);pendingPing=null;$('ping').disabled=false;$('reply').textContent='PONG received. Two-way communication works.';}}}
$('connect').onclick=async()=>{ $('connect').disabled=true;try{
device=await navigator.bluetooth.requestDevice({filters:[{services:[SERVICE]}]});
device.addEventListener('gattserverdisconnected',()=>{reset();log('Bluetooth disconnected.');},{once:true});
const server=await device.gatt.connect(), service=await server.getPrimaryService(SERVICE);
command=await service.getCharacteristic(COMMAND);status=await service.getCharacteristic(STATUS);
status.addEventListener('characteristicvaluechanged',received);await status.startNotifications();
$('connection').textContent='Connected · '+(device.name||'CLAW-Pi');$('disconnect').disabled=false;$('ping').disabled=false;log('Connected and subscribed to updates.');
}catch(e){if(device?.gatt?.connected)device.gatt.disconnect();reset();log(e.message);}};
$('disconnect').onclick = async () => {
  const currentDevice = device;
  const currentStatus = status;

  $('disconnect').disabled = true;
  $('ping').disabled = true;

  try {
    if (currentDevice?.gatt?.connected && currentStatus) {
      await currentStatus.stopNotifications();
      currentStatus.removeEventListener(
        'characteristicvaluechanged',
        received
      );
      log('Notifications stopped.');
    }
  } catch (error) {
    log('Notification cleanup: ' + error.message);
  } finally {
    currentDevice?.gatt?.disconnect();
    reset();
  }
};
$('ping').onclick=async()=>{if(!command)return;$('ping').disabled=true;try{await command.writeValueWithResponse(new TextEncoder().encode('PING'));$('reply').textContent='Waiting for PONG…';pendingPing=setTimeout(()=>{pendingPing=null;$('ping').disabled=!command;$('reply').textContent='No PONG received within 5 seconds.';},5000);log('Phone → PING');}catch(e){$('ping').disabled=!command;log(e.message);}};
const FILES=['./','./index.html','./style.css','./app.js','./manifest.webmanifest','./icon.svg','./icon-192.png','./icon-512.png'], CACHE='claw-link-v1';
async function checkCache(){const cache=await caches.open(CACHE);const ok=(await Promise.all(FILES.map(f=>cache.match(new URL(f,location.href))))).every(Boolean);$('cache').textContent=ok?'Offline files verified locally. Reopen offline to test.':'Offline files incomplete. Tap Save for offline.';return ok;}
async function setupOffline(){try{if(!('serviceWorker'in navigator))throw Error('Service workers unavailable in this browser.');await navigator.serviceWorker.register('./sw.js');await navigator.serviceWorker.ready;await checkCache();}catch(e){$('cache').textContent=e.message;log('Offline: '+e.message);}}
$('save').onclick=async()=>{ $('save').disabled=true;try{await setupOffline();const cache=await caches.open(CACHE);await cache.addAll(FILES);await checkCache();}catch(e){log('Offline save failed: '+e.message);$('cache').textContent='Save failed. Keep internet on and retry.';}finally{$('save').disabled=false;}};
$('protect').onclick=async()=>{try{if(!navigator.storage?.persist)throw Error('Persistent storage is unavailable here.');const ok=await navigator.storage.persist();$('storage').textContent=ok?'Storage protection granted.':'Protection not granted. Offline files remain browser-managed.';}catch(e){$('storage').textContent=e.message;}};
function network(){$('network').textContent=navigator.onLine?'Network available':'Offline';}
addEventListener('online',network);addEventListener('offline',network);network();
$('support').textContent=navigator.bluetooth?'Bluetooth API detected. Start the Pi server before connecting.':'Bluetooth API unavailable. Use Android Chrome or try iPhone Bluefy.';
reset();setupOffline();
if(navigator.storage?.persisted)navigator.storage.persisted().then(p=>$('storage').textContent=p?'Storage protection granted.':'Storage protection not granted yet.').catch(()=>{});
setInterval(()=>{$('freshness').textContent=lastSeen?((Date.now()-lastSeen>3500?'Updates stale · ':'Last update · ')+Math.floor((Date.now()-lastSeen)/1000)+'s ago'):'No live data received';},1000);
