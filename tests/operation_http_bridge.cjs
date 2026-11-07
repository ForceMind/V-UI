'use strict';
// The shipped controller talks through a pipe to the fixture's authenticated
// real HTTP client. No API success, revisions, certificate or runtime is mocked.
const {createInterface}=require('node:readline');
const {Flow}=require('../web/js/operation-flow.js');
const lines=createInterface({input:process.stdin,crlfDelay:Infinity});
const pending=[];let configuration;
const send=value=>process.stdout.write(JSON.stringify(value)+'\n');
lines.on('line',line=>{
    const value=JSON.parse(line);
    if(!configuration){configuration=value;void run();}
    else pending.shift()?.(value);
});
async function request(path,method='GET',body=null,{headers={},fullResponse=false}={}){
    const response=new Promise(resolve=>pending.push(resolve));send({request:{path,method,body,headers,fullResponse}});
    const reply=await response;
    if(reply.status<200||reply.status>=300)throw Object.assign(new Error('Real HTTP operation rejected'),{status:reply.status,detail:reply.value?.detail});
    return fullResponse?{value:reply.value,nodeRevision:reply.nodeRevision}:reply.value;
}
async function run(){
    try{
        const flow=new Flow(request);await flow.load();
        const choice={revision:flow.snapshot.revision,name:'简单操作-'+configuration.task,port:configuration.port,
            certificateId:flow.certificates[0]?.id,upstreamId:flow.snapshot.settings.selected_upstream_id,
            link:configuration.link||'',localPort:configuration.localPort};
        if(flow.needsImpact(choice))choice.impactStamp=flow.impactStamp();
        const result=await flow.run(configuration.task,choice);
        if(configuration.task==='host')result.connection=await flow.localConnection();
        send({result});
    }catch(error){send({error:{code:error.code||'HTTP_ERROR',status:error.status||null}});process.exitCode=1;}
    finally{lines.close();}
}
