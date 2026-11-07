'use strict';
// Small synthetic DOM for production event/controller tests, never rendering.
class Element {
    constructor(tag, document) {
        this.tagName=tag.toLowerCase();this.ownerDocument=document;this.children=[];this.parentNode=null;
        this.attributes={};this.dataset={};this.events={};this.className='';this.id='';this.hidden=false;
        this.disabled=false;this.checked=false;this.readOnly=false;this.type='';this.open=false;
        this._text='';this._value='';this._assigned=false;this.style={};
        this.classList={add:(...names)=>{this.className=Array.from(new Set([...this.className.split(/\s+/).filter(Boolean),...names])).join(' ')},
            remove:(...names)=>{this.className=this.className.split(/\s+/).filter(x=>!names.includes(x)).join(' ')},
            contains:name=>this.className.split(/\s+/).includes(name),
            toggle:(name,force)=>{const yes=force??!this.classList.contains(name);if(yes)this.classList.add(name);else this.classList.remove(name);return yes;}};
    }
    get childNodes(){return this.children;}
    get textContent(){return this._text+this.children.map(c=>c.textContent).join('');}
    set textContent(value){this.replaceChildren();this._text=String(value??'');}
    get value(){if(this.tagName==='select'&&!this._assigned)return this.children[0]?.value||'';return this._value;}
    set value(value){this._value=String(value??'');this._assigned=true;}
    get options(){return this.tagName==='select'?this.children:undefined;}
    get previousElementSibling(){const i=this.parentNode?.children.indexOf(this);return i>0?this.parentNode.children[i-1]:null;}
    append(...nodes){for(let node of nodes){if(typeof node==='string'){const text=new Element('text',this.ownerDocument);text.textContent=node;node=text;}if(node.parentNode)node.remove();node.parentNode=this;this.children.push(node);}}
    appendChild(node){this.append(node);return node;}
    replaceChildren(...nodes){for(const child of this.children)child.parentNode=null;this.children=[];this._text='';this.append(...nodes);}
    remove(index){if(index!==undefined){this.children[index]?.remove();return;}if(this.parentNode){const i=this.parentNode.children.indexOf(this);this.parentNode.children.splice(i,1);this.parentNode=null;}}
    setAttribute(name,value){this.attributes[name]=String(value);if(name==='id')this.id=String(value);if(name==='class')this.className=String(value);if(name.startsWith('data-'))this.dataset[name.slice(5).replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]=String(value);}
    getAttribute(name){return name==='id'?this.id:this.attributes[name];}
    addEventListener(name,fn){(this.events[name]??=[]).push(fn);}
    removeEventListener(name,fn){this.events[name]=(this.events[name]||[]).filter(f=>f!==fn);}
    dispatchEvent(event){event.target??=this;event.preventDefault??=()=>{event.defaultPrevented=true};for(const fn of this.events[event.type]||[])fn(event);if(event.bubbles&&this.parentNode)this.parentNode.dispatchEvent(event);return !event.defaultPrevented;}
    click(){if(!this.disabled)this.dispatchEvent({type:'click',bubbles:true});}
    matches(selector){
        if(selector.includes(','))return selector.split(',').some(s=>this.matches(s.trim()));
        let checked=selector.endsWith(':checked');if(checked)selector=selector.slice(0,-8);
        if(checked&&!this.checked)return false;
        const attribute=selector.match(/\[([^=\]]+)(?:=["']?([^\]"']+)["']?)?\]/);
        if(attribute){const [,name,value]=attribute;const actual=name in this?this[name]:this.getAttribute(name);if(value===undefined?actual==null:String(actual)!==value)return false;selector=selector.replace(attribute[0],'');}
        if(selector.startsWith('.'))return this.classList.contains(selector.slice(1));
        if(selector.startsWith('#'))return this.id===selector.slice(1);
        return !selector||this.tagName===selector.toLowerCase();
    }
    querySelectorAll(selector){const found=[];for(const child of this.children){if(child.matches(selector))found.push(child);found.push(...child.querySelectorAll(selector));}return found;}
    querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
    closest(selector){for(let node=this;node;node=node.parentNode)if(node.matches(selector))return node;return null;}
    focus(){this.ownerDocument.activeElement=this;}
    select(){this.selected=true;}
    showModal(){this.open=true;}
    close(){this.open=false;this.dispatchEvent({type:'close'});}
}
class Document extends Element {
    constructor(){super('document',null);this.ownerDocument=this;this.readyState='loading';this.body=new Element('body',this);this.append(this.body);}
    createElement(tag){return new Element(tag,this);}
    getElementById(id){return this.querySelector('#'+id);}
}
module.exports={Document,Element};
