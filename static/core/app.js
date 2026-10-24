document.addEventListener('DOMContentLoaded',()=>{
  const $=(s,r=document)=>r.querySelector(s);
  const $$=(s,r=document)=>[...r.querySelectorAll(s)];
  const fmtMoney=v=>new Intl.NumberFormat('en-US',{maximumFractionDigits:0}).format(Number(v||0));
  const fmtQty=v=>{const n=Number(v||0);return Number.isInteger(n)?String(n):n.toFixed(3).replace(/0+$/,'').replace(/\.$/,'');};
  const num=v=>{const n=Number(String(v??'').replace(/,/g,''));return Number.isFinite(n)?n:0;};
  const esc=v=>String(v??'').replace(/[&<>\"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[ch]));
  const csrf=()=>document.querySelector('[name=csrfmiddlewaretoken]')?.value||'';

  const iconPaths={
    dashboard:'<path d="M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z"/>',
    pos:'<path d="M4 5h16v14H4z"/><path d="M7 9h10M7 13h4M15 13h2M7 17h3M13 17h4"/>',
    products:'<path d="m4 8 8-4 8 4-8 4-8-4Z"/><path d="m4 8 8 4 8-4v9l-8 4-8-4V8Z"/>',
    stock:'<path d="M4 7h16v13H4z"/><path d="M7 4h10l2 3H5l2-3ZM8 11h8M8 15h8"/>',
    purchases:'<path d="M12 3v12M8 11l4 4 4-4"/><path d="M4 20h16"/>',
    sales:'<path d="M6 18 18 6M11 6h7v7"/>',
    expenses:'<path d="M4 7h16v12H4z"/><path d="M4 10h16M8 15h4"/>',
    reports:'<path d="M4 20V11M10 20V5M16 20v-7M21 20H3"/>',
    users:'<path d="M15 20v-1.5a4 4 0 0 0-4-4H6.5a4 4 0 0 0-4 4V20"/><circle cx="8.75" cy="7" r="3.5"/><path d="M16 4.2a3.5 3.5 0 0 1 0 6.6M21.5 20v-1.5a4 4 0 0 0-3-3.65"/>',
    debts:'<path d="M4 6h16v12H4z"/><path d="M8 10h8M8 14h5"/><circle cx="17" cy="14" r="1"/>',
    settings:'<circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 0 0-.1-1l2-1.5-2-3.4-2.4 1A7 7 0 0 0 14.8 6l-.3-2.6h-5L9.2 6a7 7 0 0 0-1.7 1.1l-2.4-1-2 3.4L5.1 11a7 7 0 0 0 0 2l-2 1.5 2 3.4 2.4-1A7 7 0 0 0 9.2 18l.3 2.6h5l.3-2.6a7 7 0 0 0 1.7-1.1l2.4 1 2-3.4-2-1.5c.1-.3.1-.7.1-1Z"/>',
    logout:'<path d="M10 17l5-5-5-5M15 12H4"/><path d="M14 4h6v16h-6"/>',
    menu:'<path d="M4 7h16M4 12h16M4 17h16"/>'
  };
  $$('[data-icon]').forEach(el=>{const p=iconPaths[el.dataset.icon];if(p)el.innerHTML=`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${p}</svg>`;});

  const dateEl=$('#liveDate');
  if(dateEl) dateEl.textContent=new Intl.DateTimeFormat('en-GB',{day:'2-digit',month:'short',year:'numeric'}).format(new Date());

  /* Main shell: desktop collapse, mobile drawer and top-right user menu. */
  const menu=$('#menuBtn'), sidebar=$('#sidebar'), overlay=$('#sidebarOverlay');
  const desktopSidebar=window.matchMedia('(min-width: 861px)');

  const closeMobileMenu=()=>{
    sidebar?.classList.remove('open');
    overlay?.classList.remove('open');
  };

  const readSidebarPreference=()=>{
    try{return localStorage.getItem('mfp_sidebar_collapsed')==='1';}catch(_){return false;}
  };

  const setDesktopSidebar=(collapsed,persist=false)=>{
    document.body.classList.toggle('sidebar-collapsed',Boolean(collapsed));
    menu?.setAttribute('aria-expanded',collapsed?'false':'true');
    menu?.setAttribute('aria-label',collapsed?'Open sidebar':'Close sidebar');
    if(persist){
      try{localStorage.setItem('mfp_sidebar_collapsed',collapsed?'1':'0');}catch(_){}
    }
  };

  const syncSidebarMode=()=>{
    if(desktopSidebar.matches){
      closeMobileMenu();
      setDesktopSidebar(readSidebarPreference());
    }else{
      document.body.classList.remove('sidebar-collapsed');
      menu?.setAttribute('aria-expanded',sidebar?.classList.contains('open')?'true':'false');
      menu?.setAttribute('aria-label',sidebar?.classList.contains('open')?'Close menu':'Open menu');
    }
  };

  menu?.addEventListener('click',()=>{
    if(desktopSidebar.matches){
      const collapsed=!document.body.classList.contains('sidebar-collapsed');
      setDesktopSidebar(collapsed,true);
      return;
    }
    const opening=!sidebar?.classList.contains('open');
    sidebar?.classList.toggle('open',opening);
    overlay?.classList.toggle('open',opening);
    menu.setAttribute('aria-expanded',opening?'true':'false');
    menu.setAttribute('aria-label',opening?'Close menu':'Open menu');
  });

  overlay?.addEventListener('click',closeMobileMenu);
  $$('.nav-item,.nav-subitem').forEach(link=>link.addEventListener('click',()=>{if(!desktopSidebar.matches)closeMobileMenu();}));
  desktopSidebar.addEventListener?.('change',syncSidebarMode);
  syncSidebarMode();

  /* Sidebar dropdown groups. Keep the navigation compact and open one group at a time. */
  const navGroups=$$('.nav-group');
  const setNavGroup=(group,open)=>{
    if(!group)return;
    group.classList.toggle('open',Boolean(open));
    group.querySelector('.nav-group-toggle')?.setAttribute('aria-expanded',open?'true':'false');
  };
  const openOnlyNavGroup=group=>{
    navGroups.forEach(item=>setNavGroup(item,item===group));
  };
  navGroups.forEach(group=>{
    const toggle=group.querySelector('.nav-group-toggle');
    toggle?.addEventListener('click',()=>{
      if(desktopSidebar.matches&&document.body.classList.contains('sidebar-collapsed')){
        setDesktopSidebar(false,true);
        requestAnimationFrame(()=>openOnlyNavGroup(group));
        return;
      }
      const willOpen=!group.classList.contains('open');
      if(willOpen)openOnlyNavGroup(group);
      else setNavGroup(group,false);
    });
  });

  const userMenuWrap=$('#userMenuWrap'), userMenuBtn=$('#userMenuBtn'), userMenu=$('#userMenu');
  const setUserMenu=open=>{
    if(!userMenuBtn||!userMenu)return;
    userMenuBtn.setAttribute('aria-expanded',open?'true':'false');
    userMenu.setAttribute('aria-hidden',open?'false':'true');
    userMenu.classList.toggle('open',open);
  };
  userMenuBtn?.addEventListener('click',e=>{
    e.stopPropagation();
    setUserMenu(!userMenu?.classList.contains('open'));
  });
  document.addEventListener('click',e=>{
    if(userMenu?.classList.contains('open')&&!userMenuWrap?.contains(e.target))setUserMenu(false);
  });
  document.addEventListener('keydown',e=>{
    if(e.key==='Escape'){
      setUserMenu(false);
      if(!desktopSidebar.matches)closeMobileMenu();
    }
  });

  $$('[data-password-toggle]').forEach(btn=>btn.addEventListener('click',()=>{
    const input=$('#'+btn.dataset.passwordToggle); if(!input)return;
    input.type=input.type==='password'?'text':'password'; btn.textContent=input.type==='password'?'Show':'Hide';
  }));

  /* Custom dropdowns so Windows/OS select styling is not shown. */
  /* NOVMAN: SINGLE ORIGINAL STYLED DROPDOWN + SEARCH INSIDE THE SAME MENU */
  const customSelects=[];
  const normalizeSelectText=value=>String(value??'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLocaleLowerCase().trim();
  const SEARCH_LIMIT=80;
  function closeSelects(except=null){
    customSelects.forEach(state=>{
      if(state!==except){state.wrap.classList.remove('open','open-upward');state.button.setAttribute('aria-expanded','false');}
    });
  }
  function enhanceSelect(select){
    if(select.dataset.mfpEnhanced==='1')return;
    select.dataset.mfpEnhanced='1';
    select.classList.add('mfp-native-select');
    const wrap=document.createElement('div');wrap.className='mfp-select';
    const button=document.createElement('button');button.type='button';button.className='mfp-select-button';button.setAttribute('aria-haspopup','listbox');button.setAttribute('aria-expanded','false');
    const menu=document.createElement('div');menu.className='mfp-select-menu';
    const searchRow=document.createElement('div');searchRow.className='mfp-select-search';
    const search=document.createElement('input');search.type='search';search.className='mfp-select-search-input';search.autocomplete='off';search.spellcheck=false;
    search.placeholder='Search name, barcode or ID...';search.setAttribute('aria-label','Search dropdown options');
    const info=document.createElement('div');info.className='mfp-select-search-info';
    const optionsBox=document.createElement('div');optionsBox.className='mfp-select-options';optionsBox.setAttribute('role','listbox');
    searchRow.appendChild(search);menu.append(searchRow,info,optionsBox);wrap.append(button,menu);
    select.insertAdjacentElement('afterend',wrap);
    const state={select,wrap,button,menu,search,optionsBox};customSelects.push(state);
    let visibleButtons=[],keyboardIndex=0;

    function paintFocus(){
      visibleButtons.forEach((el,i)=>el.classList.toggle('key-focused',i===keyboardIndex));
      visibleButtons[keyboardIndex]?.scrollIntoView({block:'nearest'});
    }
    function showOptions(){
      const choices=[...select.options];
      const term=normalizeSelectText(search.value);
      const matches=choices.filter(opt=>{
        const extras=[opt.dataset.search,opt.dataset.barcode,opt.dataset.sku,opt.dataset.productId,opt.value].filter(Boolean).join(' ');
        return !term||normalizeSelectText(opt.textContent+' '+extras).includes(term);
      });
      const visible=matches.slice(0,SEARCH_LIMIT);
      optionsBox.replaceChildren();visibleButtons=[];
      keyboardIndex=0;
      if(!matches.length){
        const empty=document.createElement('div');empty.className='mfp-select-no-match';empty.textContent='No matching items';optionsBox.appendChild(empty);
      }
      const fragment=document.createDocumentFragment();
      visible.forEach((opt,i)=>{
        const item=document.createElement('button');item.type='button';
        item.className='mfp-select-option'+(opt.selected?' active':'');
        item.textContent=opt.textContent;
        item.disabled=opt.disabled;
        item.dataset.value=opt.value;
        item.setAttribute('role','option');item.setAttribute('aria-selected',String(Boolean(opt.selected)));
        item.addEventListener('click',e=>{
          e.stopPropagation();
          select.value=opt.value;
          select.dispatchEvent(new Event('input',{bubbles:true}));
          select.dispatchEvent(new Event('change',{bubbles:true}));
          closeSelects();button.focus();
        });
        fragment.appendChild(item);visibleButtons.push(item);
      });
      optionsBox.appendChild(fragment);
      info.textContent=matches.length>SEARCH_LIMIT
        ?`${matches.length} matches. Showing ${SEARCH_LIMIT}; type more to narrow.`
        :term?`${matches.length} match${matches.length===1?'':'es'}`:'';
    }
    function render(){
      const all=[...select.options];
      const selected=select.options[select.selectedIndex]||all[0];
      button.textContent=selected?.textContent||'Select';
      button.disabled=select.disabled;
      const name=(select.name||'').toLowerCase();
      const useful=all.length>=10||['product','product_id','category','supplier','supplier_id'].includes(name)||select.hasAttribute('data-novman-search');
      searchRow.classList.toggle('hidden',!useful);
      if(!useful)search.value='';
      showOptions();
    }
    function openDropdown(){
      if(select.disabled)return;
      closeSelects(state);
      wrap.classList.add('open');button.setAttribute('aria-expanded','true');
      const bounds=button.getBoundingClientRect();
      wrap.classList.toggle('open-upward',bounds.bottom+350>window.innerHeight&&bounds.top>350);
      search.value='';render();
      if(!searchRow.classList.contains('hidden'))search.focus({preventScroll:true});
    }
    button.addEventListener('click',e=>{
      e.stopPropagation();
      if(wrap.classList.contains('open')){closeSelects();return;}
      openDropdown();
    });
    button.addEventListener('keydown',e=>{
      if(['ArrowDown','ArrowUp','Enter',' '].includes(e.key)&&!wrap.classList.contains('open')){
        e.preventDefault();openDropdown();
      }else if(e.key==='Escape')closeSelects();
    });
    searchRow.addEventListener('click',e=>e.stopPropagation());
    menu.addEventListener('click',e=>e.stopPropagation());
    search.addEventListener('input',showOptions);
    search.addEventListener('keydown',e=>{
      if(e.key==='Escape'){e.preventDefault();closeSelects();button.focus();}
      else if(e.key==='ArrowDown'&&visibleButtons.length){e.preventDefault();keyboardIndex=Math.min(visibleButtons.length-1,keyboardIndex+1);paintFocus();}
      else if(e.key==='ArrowUp'&&visibleButtons.length){e.preventDefault();keyboardIndex=Math.max(0,keyboardIndex-1);paintFocus();}
      else if(e.key==='Enter'&&visibleButtons.length){e.preventDefault();visibleButtons[keyboardIndex].click();}
    });
    select.addEventListener('change',render);
    new MutationObserver(render).observe(select,{childList:true,subtree:true,attributes:true,characterData:true});
    render();
  }
  $$('select.form-control').forEach(enhanceSelect);
  document.addEventListener('click',()=>closeSelects());

  /* HAMA Transport expenses: date-first trip options, same global JS and styled dropdown. */
  const hamaExpenseForm=$('[data-hama-expense-form]');
  if(hamaExpenseForm){
    const day=$('[name="expense_date"]',hamaExpenseForm);
    const trip=$('[name="trip"]',hamaExpenseForm);
    const endpoint=hamaExpenseForm.dataset.tripsUrl;
    let requestId=0;
    day?.addEventListener('change',async()=>{
      if(!trip||!endpoint)return;
      const id=++requestId;
      trip.disabled=true;
      try{
        const response=await fetch(endpoint+'?date='+encodeURIComponent(day.value),{
          headers:{'Accept':'application/json'},credentials:'same-origin'
        });
        if(!response.ok)throw Error('Could not load trips');
        const data=await response.json();
        if(id!==requestId)return;
        trip.replaceChildren(new Option('General expense (no specific trip)',''));
        (data.trips||[]).forEach(item=>trip.add(new Option(item.label,String(item.id))));
        trip.value='';
        trip.dispatchEvent(new Event('change',{bubbles:true}));
      }catch(error){
        if(id===requestId){
          trip.replaceChildren(new Option('Could not load trips – check the date',''));
          trip.value='';
          trip.dispatchEvent(new Event('change',{bubbles:true}));
        }
      }finally{if(id===requestId)trip.disabled=false;}
    });
  }

  /* Modals */
  let lastModalTrigger=null;
  function openModal(id,trigger){const modal=$('#'+id);if(!modal)return;lastModalTrigger=trigger||null;closeSelects();modal.classList.add('open');document.body.style.overflow='hidden';setTimeout(()=>modal.querySelector('input:not([type=hidden]),button,select,textarea')?.focus(),40);}
  function closeModal(id){const modal=$('#'+id);if(!modal)return;modal.classList.remove('open');if(!$('.modal.open'))document.body.style.overflow='';lastModalTrigger?.focus?.();}
  $$('[data-open-modal]').forEach(btn=>btn.addEventListener('click',()=>openModal(btn.dataset.openModal,btn)));
  $$('[data-close-modal]').forEach(btn=>btn.addEventListener('click',()=>closeModal(btn.dataset.closeModal)));
  $$('.modal').forEach(modal=>modal.addEventListener('mousedown',e=>{if(e.target===modal)closeModal(modal.id);}));
  document.addEventListener('keydown',e=>{if(e.key==='Escape'){const open=$('.modal.open');if(open)closeModal(open.id);else closeSelects();}});

  /* Reusable confirmation modal for delete/archive actions. */
  const confirmForm=$('#confirmActionForm'), confirmTitle=$('#confirmActionTitle'), confirmText=$('#confirmActionText'), confirmSubmit=$('#confirmActionSubmit');
  $$('[data-confirm-url]').forEach(btn=>btn.addEventListener('click',()=>{
    if(btn.disabled)return;
    if(confirmForm)confirmForm.action=btn.dataset.confirmUrl||'';
    if(confirmTitle)confirmTitle.textContent=btn.dataset.confirmTitle||'Confirm action';
    if(confirmText)confirmText.textContent=btn.dataset.confirmText||'Please confirm this action.';
    if(confirmSubmit)confirmSubmit.textContent=btn.dataset.confirmLabel||'Confirm';
    openModal('confirmActionModal',btn);
  }));

  /* Customer debt payment void modal. */
  const voidPaymentForm=$('#voidPaymentForm'), voidPaymentText=$('#voidPaymentText');
  $$('[data-payment-void-url]').forEach(btn=>btn.addEventListener('click',()=>{
    if(voidPaymentForm)voidPaymentForm.action=btn.dataset.paymentVoidUrl||'';
    if(voidPaymentText)voidPaymentText.textContent=`Void ${btn.dataset.paymentAmount||''} TZS payment and restore it to the customer balance?`;
    openModal('voidPaymentModal',btn);
  }));

  /* Ninaowadai manual receivable payment modal. */
  const receivablePaymentForm=$('#receivablePaymentForm');
  const receivablePaymentName=$('#receivablePaymentName');
  const receivablePaymentBalance=$('#receivablePaymentBalance');
  const receivablePaymentAmount=$('#receivablePaymentAmount');

  $$('[data-receivable-payment-url]').forEach(btn=>btn.addEventListener('click',()=>{
    const balance=Math.max(0,num(btn.dataset.receivableBalance));
    if(receivablePaymentForm)receivablePaymentForm.action=btn.dataset.receivablePaymentUrl||'';
    if(receivablePaymentName)receivablePaymentName.textContent=btn.dataset.receivableName||'Record money received.';
    if(receivablePaymentBalance)receivablePaymentBalance.textContent=`${fmtMoney(balance)} TZS`;
    if(receivablePaymentAmount){
      receivablePaymentAmount.value='';
      receivablePaymentAmount.max=String(balance);
    }
    openModal('receivablePaymentModal',btn);
    setTimeout(()=>receivablePaymentAmount?.focus(),60);
  }));

  /* Settings logo preview */
  const logoInput=$('#id_logo'), logoPreview=$('#logoPreview');
  logoInput?.addEventListener('change',()=>{
    const file=logoInput.files?.[0]; if(!file||!logoPreview)return;
    if(!file.type.startsWith('image/'))return;
    const reader=new FileReader(); reader.onload=()=>{logoPreview.innerHTML=`<img src="${reader.result}" alt="Logo preview">`;}; reader.readAsDataURL(file);
  });

  $$('[data-raw-money]').forEach(input=>{
    input.addEventListener('focus',()=>{input.value=input.dataset.rawMoney||input.value.replace(/,/g,'');});
    input.addEventListener('blur',()=>{const n=num(input.value);input.dataset.rawMoney=n;input.value=fmtMoney(n);});
  });

  const adjustType=$('#adjustType'), adjustCost=$('#adjustCostWrap');
  const syncAdjust=()=>{if(adjustType&&adjustCost)adjustCost.classList.toggle('hidden',adjustType.value!=='increase');};
  if(adjustType){adjustType.addEventListener('change',syncAdjust);syncAdjust();}

  const openingProduct=$('#openingProduct'), openingUnit=$('#openingUnit');
  function syncOpeningUnits(){
    if(!openingProduct||!openingUnit||!window.MFP_OPENING_UNITS)return;
    const units=window.MFP_OPENING_UNITS[openingProduct.value]||[];
    openingUnit.innerHTML=units.length?units.map(u=>`<option value="${u.id}">${u.name} (${u.symbol}) · ${fmtQty(u.conversion)} base</option>`).join(''):'<option value="">No selling units</option>';
    openingUnit.dispatchEvent(new Event('change',{bubbles:true}));
  }
  if(openingProduct){openingProduct.addEventListener('change',syncOpeningUnits);syncOpeningUnits();}

  const purchaseProduct=$('#purchaseProduct'), purchaseUnit=$('#purchaseUnit');
  function syncPurchaseUnits(){
    if(!purchaseProduct||!purchaseUnit||!window.MFP_PURCHASE_UNITS)return;
    const units=window.MFP_PURCHASE_UNITS[purchaseProduct.value]||[];
    purchaseUnit.innerHTML=units.length?units.map(u=>`<option value="${u.id}">${u.name} (${u.symbol}) · ${fmtQty(u.conversion)} base</option>`).join(''):'<option value="">No selling units</option>';
    purchaseUnit.dispatchEvent(new Event('change',{bubbles:true}));
  }
  if(purchaseProduct){purchaseProduct.addEventListener('change',syncPurchaseUnits);syncPurchaseUnits();}

  /* POS */
  if(window.MFP_PRODUCTS){
    const products=Array.isArray(window.MFP_PRODUCTS)?window.MFP_PRODUCTS:[];
    const grid=$('#productGrid'), search=$('#posSearch'), categoryBar=$('#posCategories'), productCount=$('#productCount'), productScroll=$('#productScroll');
    const modal=$('#addModal'), unitSel=$('#modalUnit'), qtyIn=$('#modalQty'), discIn=$('#modalDiscount'), modalName=$('#modalProductName'), modalStock=$('#modalStock'), modalTotal=$('#modalLineTotal');
    const cartLines=$('#cartLines'), subtotalEl=$('#cartSubtotal'), itemDiscEl=$('#cartItemDiscount'), grandEl=$('#cartGrand'), saleDisc=$('#saleDiscount'), cartCount=$('#cartCount'), errorEl=$('#posError');
    const customerName=$('#customerName'), customerPhone=$('#customerPhone'), paymentMethod=$('#paymentMethod'), debtNote=$('#posDebtNote');
    let selected=null, cart=[], activeCategory='all';

    const defaultUnit=p=>(p.units||[]).find(u=>u.default)||(p.units||[])[0];
    const categoryName=p=>String(p.category||'Uncategorized').trim()||'Uncategorized';

    function renderCategoryButtons(){
      if(!categoryBar)return;
      const categories=[...new Set(products.map(categoryName))].sort((a,b)=>a.localeCompare(b));
      categoryBar.innerHTML='';
      const choices=[{key:'all',label:'All'},...categories.map(name=>({key:name.toLowerCase(),label:name}))];
      choices.forEach(choice=>{
        const button=document.createElement('button');
        button.type='button';
        button.className='category-chip'+(activeCategory===choice.key?' active':'');
        button.textContent=choice.label;
        button.setAttribute('aria-pressed',activeCategory===choice.key?'true':'false');
        button.addEventListener('click',()=>{
          activeCategory=choice.key;
          $$('.category-chip',categoryBar).forEach(btn=>{
            const isActive=btn===button;
            btn.classList.toggle('active',isActive);
            btn.setAttribute('aria-pressed',isActive?'true':'false');
          });
          renderProducts(true);
        });
        categoryBar.appendChild(button);
      });
    }

    function filteredProducts(){
      const q=String(search?.value||'').toLowerCase().trim();
      return products.filter(p=>{
        const category=categoryName(p);
        if(activeCategory!=='all'&&category.toLowerCase()!==activeCategory)return false;
        if(!q)return true;
        const unitText=(p.units||[]).map(u=>`${u.name||''} ${u.symbol||''}`).join(' ');
        const haystack=`${p.name||''} ${p.barcode||''} ${p.id||''} ${category} ${p.base_unit||''} ${unitText}`.toLowerCase();
        return haystack.includes(q);
      });
    }

    function renderProducts(resetScroll=false){
      if(!grid)return;
      const list=filteredProducts();
      if(productCount)productCount.textContent=`${list.length} ${list.length===1?'product':'products'}`;
      if(!products.length){
        grid.innerHTML='<div class="empty-block pos-empty">No active products with an active selling unit. Add a selling unit to a product first.</div>';
        return;
      }
      if(!list.length){
        grid.innerHTML='<div class="empty-block pos-empty">No matching product.</div>';
        return;
      }

      grid.innerHTML=list.map(p=>{
        const u=defaultUnit(p);
        if(!u)return '';
        const unitCount=(p.units||[]).length;
        const outOfStock=num(p.stock)<=0;
        return `<button class="product-card${outOfStock?' out-of-stock':''}" type="button" data-product="${Number(p.id)}" ${outOfStock?'disabled':''}>
          <span class="product-card-head">
            <span class="product-name">${esc(p.name)}</span>
            <span class="product-unit-count">${unitCount} ${unitCount===1?'unit':'units'}</span>
          </span>
          <span class="product-category">${esc(categoryName(p))}</span>
          <span class="product-bottom">
            <span class="product-stock">Stock ${fmtQty(p.stock)} ${esc(p.base_unit)}</span>
            <span class="product-price">${fmtMoney(u.price)} / ${esc(u.symbol)}</span>
          </span>
        </button>`;
      }).join('');

      $$('[data-product]',grid).forEach(btn=>btn.addEventListener('click',()=>openProduct(Number(btn.dataset.product),btn)));
      if(resetScroll&&productScroll)productScroll.scrollTop=0;
    }

    function openProduct(id,trigger){
      selected=products.find(p=>Number(p.id)===id);
      if(!selected)return;
      modalName.textContent=selected.name;
      modalStock.textContent=`Available ${fmtQty(selected.stock)} ${selected.base_unit}`;
      unitSel.innerHTML=(selected.units||[]).map(u=>`<option value="${Number(u.id)}" ${u.default?'selected':''}>${esc(u.name)} · ${fmtMoney(u.price)} TZS</option>`).join('');
      unitSel.dispatchEvent(new Event('change',{bubbles:true}));
      qtyIn.value=''; discIn.value=''; errorEl.textContent=''; calcModal();
      openModal('addModal',trigger);
      setTimeout(()=>qtyIn.focus(),60);
    }

    function calcModal(){
      if(!selected)return;
      const u=(selected.units||[]).find(x=>Number(x.id)===Number(unitSel.value))||(selected.units||[])[0];
      if(!u)return;
      const total=Math.max(0,num(qtyIn.value)*num(u.price)-num(discIn.value));
      modalTotal.textContent=`${fmtMoney(total)} TZS`;
    }
    [unitSel,qtyIn,discIn].forEach(el=>el?.addEventListener('input',calcModal));

    function addLine(){
      if(!selected)return;
      const u=(selected.units||[]).find(x=>Number(x.id)===Number(unitSel.value));
      if(!u)return;
      const qty=num(qtyIn.value), discount=num(discIn.value);
      if(qty<=0){qtyIn.focus();return;}
      const subtotal=qty*num(u.price);
      if(discount<0||discount>subtotal){errorEl.textContent='Discount cannot be greater than the item amount.';return;}
      const required=qty*num(u.conversion);
      const existing=cart.filter(x=>x.product_id===selected.id).reduce((a,x)=>a+x.quantity*x.conversion,0);
      if(existing+required>num(selected.stock)){
        errorEl.textContent=`Not enough ${selected.name}. Available ${fmtQty(selected.stock)} ${selected.base_unit}.`;
        closeModal('addModal');
        return;
      }
      const key=window.crypto?.randomUUID?window.crypto.randomUUID():String(Date.now()+Math.random());
      cart.push({key,product_id:selected.id,product_name:selected.name,unit_id:u.id,unit_name:u.name,unit_symbol:u.symbol,conversion:num(u.conversion),quantity:qty,price:num(u.price),discount});
      closeModal('addModal'); errorEl.textContent=''; renderCart();
    }
    $('#addToCart')?.addEventListener('click',addLine);
    qtyIn?.addEventListener('keydown',e=>{if(e.key==='Enter')addLine();});

    function totals(){
      const subtotal=cart.reduce((a,x)=>a+x.quantity*x.price,0);
      const itemDiscount=cart.reduce((a,x)=>a+x.discount,0);
      const sd=num(saleDisc?.value);
      const grand=Math.max(0,subtotal-itemDiscount-sd);
      return{subtotal,itemDiscount,sd,grand};
    }

    function renderCart(){
      if(!cart.length){
        cartLines.innerHTML='<div class="cart-empty">No items added yet.</div>';
      }else{
        cartLines.innerHTML=cart.map(x=>`<div class="cart-line">
          <div class="cart-line-title">
            <strong>${esc(x.product_name)}</strong>
            <small>${fmtQty(x.quantity)} ${esc(x.unit_symbol)} × ${fmtMoney(x.price)}${x.discount?` · Discount ${fmtMoney(x.discount)}`:''}</small>
          </div>
          <div class="cart-line-right">
            <strong>${fmtMoney(x.quantity*x.price-x.discount)}</strong>
            <button class="cart-remove" type="button" data-remove="${esc(x.key)}">Remove</button>
          </div>
        </div>`).join('');
        $$('[data-remove]',cartLines).forEach(btn=>btn.addEventListener('click',()=>{
          cart=cart.filter(x=>x.key!==btn.dataset.remove);
          renderCart();
        }));
      }
      const t=totals();
      subtotalEl.textContent=fmtMoney(t.subtotal);
      itemDiscEl.textContent=fmtMoney(t.itemDiscount);
      grandEl.textContent=fmtMoney(t.grand);
      cartCount.textContent=`${cart.length} ${cart.length===1?'item':'items'}`;
    }

    saleDisc?.addEventListener('input',renderCart);
    $('#clearCart')?.addEventListener('click',()=>{cart=[];saleDisc.value='';errorEl.textContent='';renderCart();});
    search?.addEventListener('input',()=>renderProducts(true));

    const syncDebtCheckout=()=>{
      const isDebt=paymentMethod?.value==='debt';
      debtNote?.classList.toggle('hidden',!isDebt);
      customerName?.classList.toggle('debt-required',isDebt);
      if(customerName)customerName.placeholder=isDebt?'Customer name (required)':'Customer name (optional)';
    };
    paymentMethod?.addEventListener('change',syncDebtCheckout);
    syncDebtCheckout();

    $('#completeSale')?.addEventListener('click',async function(){
      errorEl.textContent='';
      if(!cart.length){errorEl.textContent='Add at least one product.';return;}
      if(paymentMethod?.value==='debt'&&!String(customerName?.value||'').trim()){
        errorEl.textContent='Customer name is required for a debt sale.';
        customerName?.focus();
        return;
      }
      const t=totals();
      if(t.sd>t.subtotal-t.itemDiscount){errorEl.textContent='Bill discount is greater than the bill amount.';return;}
      this.disabled=true; this.textContent='Saving...';
      const fd=new FormData();
      fd.append('cart_json',JSON.stringify(cart.map(x=>({product_id:x.product_id,unit_id:x.unit_id,quantity:x.quantity,discount:x.discount}))));
      fd.append('sale_discount',t.sd);
      fd.append('payment_method',paymentMethod?.value||'cash');
      fd.append('customer_name',customerName?.value||'');
      fd.append('customer_phone',customerPhone?.value||'');
      fd.append('csrfmiddlewaretoken',csrf());
      try{
        const res=await fetch('/pos/checkout/',{method:'POST',body:fd,headers:{'X-Requested-With':'XMLHttpRequest'}});
        const data=await res.json();
        if(!res.ok||!data.ok)throw new Error(data.error||'Could not save sale.');
        window.location.href=`/sales/${data.sale_id}/`;
      }catch(e){
        errorEl.textContent=e.message;
      }finally{
        this.disabled=false; this.textContent='Complete sale';
      }
    });

    renderCategoryButtons();
    renderProducts();
    renderCart();
  }

});

/* Dashboard charts - local canvas renderer; no CDN/library required. */
(() => {
  const dataEl=document.getElementById('dashboard-data');
  if(!dataEl)return;

  let data={};
  try{data=JSON.parse(dataEl.textContent||'{}');}catch(_){return;}

  const css=(name,fallback)=>getComputedStyle(document.documentElement).getPropertyValue(name).trim()||fallback;
  const palette={
    revenue:css('--brand','#0d765e'),
    profit:'#2f9b82',
    expense:'#e0a15b',
    debt:'#849bb6',
    line:css('--line','#e3e8e6'),
    text:css('--muted','#78827e'),
    surface:css('--surface','#fff')
  };

  const fmtAxis=v=>new Intl.NumberFormat('en-US',{
    notation:Number(v)>=1000000?'compact':'standard',
    maximumFractionDigits:1
  }).format(Number(v||0));

  const fmtMoney=v=>new Intl.NumberFormat('en-US',{
    maximumFractionDigits:0
  }).format(Number(v||0));

  function prepareCanvas(canvas){
    const rect=canvas.getBoundingClientRect();
    const dpr=Math.min(window.devicePixelRatio||1,2);
    const width=Math.max(1,Math.round(rect.width));
    const height=Math.max(1,Math.round(rect.height));
    canvas.width=Math.round(width*dpr);
    canvas.height=Math.round(height*dpr);
    const ctx=canvas.getContext('2d');
    ctx.setTransform(dpr,0,0,dpr,0,0);
    return {ctx,width,height};
  }

  const trendState={
    rows:[],
    points:[],
    activeIndex:null,
    pad:null,
    plotW:0,
    width:0,
    height:0
  };

  function drawTrend(activeIndex=trendState.activeIndex){
    const canvas=document.getElementById('salesTrendChart');
    if(!canvas)return;

    const rows=Array.isArray(data.daily)?data.daily:[];
    trendState.rows=rows;
    trendState.activeIndex=activeIndex;

    const empty=document.getElementById('salesTrendEmpty');
    const total=rows.reduce(
      (sum,r)=>sum+Number(r.revenue||0)+Number(r.profit||0)+Number(r.expenses||0),
      0
    );
    empty?.classList.toggle('hidden',total>0);

    const {ctx,width,height}=prepareCanvas(canvas);
    ctx.clearRect(0,0,width,height);
    if(!rows.length)return;

    const pad={left:48,right:12,top:12,bottom:30};
    const plotW=Math.max(1,width-pad.left-pad.right);
    const plotH=Math.max(1,height-pad.top-pad.bottom);

    trendState.pad=pad;
    trendState.plotW=plotW;
    trendState.width=width;
    trendState.height=height;

    const max=Math.max(
      1,
      ...rows.flatMap(r=>[
        Number(r.revenue||0),
        Number(r.profit||0),
        Number(r.expenses||0)
      ])
    );
    const niceMax=Math.ceil(max/1000)*1000||max;

    ctx.lineWidth=1;
    ctx.strokeStyle=palette.line;
    ctx.fillStyle=palette.text;
    ctx.font='9px Inter, Segoe UI, sans-serif';
    ctx.textBaseline='middle';

    for(let i=0;i<5;i++){
      const y=pad.top+(plotH*i/4);
      const value=niceMax*(1-i/4);

      ctx.beginPath();
      ctx.moveTo(pad.left,y);
      ctx.lineTo(width-pad.right,y);
      ctx.stroke();

      ctx.textAlign='right';
      ctx.fillText(fmtAxis(value),pad.left-8,y);
    }

    const labelStep=rows.length<=8?1:Math.ceil(rows.length/7);
    rows.forEach((row,i)=>{
      const shouldLabel=i===0||i===rows.length-1||i%labelStep===0;
      if(!shouldLabel)return;
      const x=pad.left+(rows.length===1?plotW/2:(plotW*i/(rows.length-1)));
      ctx.textAlign='center';
      ctx.fillText(row.label,x,height-10);
    });

    const xForIndex=i=>pad.left+(rows.length===1?plotW/2:(plotW*i/(rows.length-1)));
    const yForValue=value=>pad.top+plotH-(Number(value||0)/niceMax*plotH);

    trendState.points=rows.map((row,i)=>({
      x:xForIndex(i),
      revenue:yForValue(row.revenue),
      profit:yForValue(row.profit),
      expenses:yForValue(row.expenses)
    }));

    /* Soft vertical guide for the selected day. */
    if(Number.isInteger(activeIndex) && trendState.points[activeIndex]){
      const x=trendState.points[activeIndex].x;

      ctx.save();
      ctx.beginPath();
      ctx.setLineDash([3,4]);
      ctx.strokeStyle='rgba(89,112,103,.28)';
      ctx.lineWidth=1;
      ctx.moveTo(x,pad.top);
      ctx.lineTo(x,height-pad.bottom);
      ctx.stroke();
      ctx.restore();
    }

    const drawSeries=(key,color,widthLine)=>{
      ctx.beginPath();

      rows.forEach((row,i)=>{
        const x=xForIndex(i);
        const y=yForValue(row[key]);
        i?ctx.lineTo(x,y):ctx.moveTo(x,y);
      });

      ctx.strokeStyle=color;
      ctx.lineWidth=widthLine;
      ctx.lineJoin='round';
      ctx.lineCap='round';
      ctx.stroke();

      rows.forEach((row,i)=>{
        const x=xForIndex(i);
        const y=yForValue(row[key]);
        const active=i===activeIndex;
        const baseRadius=key==='revenue'?2.7:2.2;
        const radius=active?baseRadius+1.8:baseRadius;

        if(active){
          ctx.beginPath();
          ctx.arc(x,y,radius+3.5,0,Math.PI*2);
          ctx.fillStyle='rgba(255,255,255,.92)';
          ctx.fill();

          ctx.beginPath();
          ctx.arc(x,y,radius+1.7,0,Math.PI*2);
          ctx.fillStyle=color;
          ctx.globalAlpha=.13;
          ctx.fill();
          ctx.globalAlpha=1;
        }

        ctx.beginPath();
        ctx.arc(x,y,radius,0,Math.PI*2);
        ctx.fillStyle=color;
        ctx.fill();

        if(active){
          ctx.beginPath();
          ctx.arc(x,y,radius,0,Math.PI*2);
          ctx.strokeStyle='#fff';
          ctx.lineWidth=1.2;
          ctx.stroke();
        }
      });
    };

    drawSeries('expenses',palette.expense,1.5);
    drawSeries('profit',palette.profit,1.7);
    drawSeries('revenue',palette.revenue,2.1);
  }

  function drawDonut(){
    const canvas=document.getElementById('paymentDonutChart');
    if(!canvas)return;

    const rows=Array.isArray(data.payments)?data.payments:[];
    const {ctx,width,height}=prepareCanvas(canvas);
    ctx.clearRect(0,0,width,height);

    const cx=width/2;
    const cy=height/2;
    const r=Math.max(20,Math.min(width,height)/2-8);
    const thickness=Math.max(12,r*.18);

    const values=rows.map(r=>Math.max(0,Number(r.value||0)));
    const total=values.reduce((a,b)=>a+b,0);
    const colors=[palette.revenue,palette.profit,palette.expense,palette.debt];

    ctx.lineWidth=thickness;
    ctx.lineCap='butt';

    if(total<=0){
      ctx.strokeStyle='#edf1ef';
      ctx.beginPath();
      ctx.arc(cx,cy,r-thickness/2,0,Math.PI*2);
      ctx.stroke();
      return;
    }

    let start=-Math.PI/2;

    values.forEach((value,i)=>{
      if(value<=0)return;
      const arc=(value/total)*Math.PI*2;
      ctx.strokeStyle=colors[i%colors.length];
      ctx.beginPath();
      ctx.arc(cx,cy,r-thickness/2,start,start+arc);
      ctx.stroke();
      start+=arc;
    });
  }

  function setupTrendHover(){
    const canvas=document.getElementById('salesTrendChart');
    const wrap=canvas?.closest('.chart-canvas-wrap');
    const tooltip=document.getElementById('salesTrendTooltip');
    if(!canvas||!wrap||!tooltip)return;

    const dateEl=tooltip.querySelector('[data-tooltip-date]');
    const salesEl=tooltip.querySelector('[data-tooltip-sales]');
    const profitEl=tooltip.querySelector('[data-tooltip-profit]');
    const expensesEl=tooltip.querySelector('[data-tooltip-expenses]');
    const netEl=tooltip.querySelector('[data-tooltip-net]');

    let lastIndex=null;

    const hide=()=>{
      lastIndex=null;
      trendState.activeIndex=null;
      tooltip.classList.remove('is-visible');
      tooltip.setAttribute('aria-hidden','true');
      drawTrend(null);
    };

    canvas.addEventListener('pointermove',event=>{
      const rows=trendState.rows;
      const pad=trendState.pad;
      if(!rows.length||!pad)return;

      const rect=canvas.getBoundingClientRect();
      const x=event.clientX-rect.left;
      const y=event.clientY-rect.top;

      const plotLeft=pad.left;
      const plotRight=trendState.width-pad.right;
      const plotTop=pad.top;
      const plotBottom=trendState.height-pad.bottom;

      if(x<plotLeft-10||x>plotRight+10||y<plotTop-14||y>plotBottom+18){
        if(lastIndex!==null)hide();
        return;
      }

      let index=0;

      if(rows.length>1){
        const ratio=Math.max(0,Math.min(1,(x-plotLeft)/Math.max(1,trendState.plotW)));
        index=Math.round(ratio*(rows.length-1));
      }

      index=Math.max(0,Math.min(rows.length-1,index));
      const row=rows[index];

      if(index!==lastIndex){
        lastIndex=index;
        trendState.activeIndex=index;
        drawTrend(index);
      }

      const net=Number(row.profit||0)-Number(row.expenses||0);

      if(dateEl)dateEl.textContent=row.label||'';
      if(salesEl)salesEl.textContent=`${fmtMoney(row.revenue)} TZS`;
      if(profitEl)profitEl.textContent=`${fmtMoney(row.profit)} TZS`;
      if(expensesEl)expensesEl.textContent=`${fmtMoney(row.expenses)} TZS`;
      if(netEl){
        netEl.textContent=`${fmtMoney(net)} TZS`;
        netEl.classList.toggle('is-negative',net<0);
      }

      const point=trendState.points[index];
      if(!point)return;

      tooltip.classList.add('is-visible');
      tooltip.setAttribute('aria-hidden','false');

      /* Position after it becomes measurable. */
      const tooltipRect=tooltip.getBoundingClientRect();
      const wrapRect=wrap.getBoundingClientRect();

      let left=point.x+14;
      if(left+tooltipRect.width>wrapRect.width-8){
        left=point.x-tooltipRect.width-14;
      }
      left=Math.max(8,Math.min(left,wrapRect.width-tooltipRect.width-8));

      const selectedYs=[
        point.revenue,
        point.profit,
        point.expenses
      ];
      let top=Math.min(...selectedYs)-16;

      if(top+tooltipRect.height>wrapRect.height-8){
        top=wrapRect.height-tooltipRect.height-8;
      }
      top=Math.max(8,top);

      tooltip.style.left=`${left}px`;
      tooltip.style.top=`${top}px`;
    });

    canvas.addEventListener('pointerleave',hide);
    canvas.addEventListener('pointercancel',hide);
  }

  let raf=0;

  const render=()=>{
    cancelAnimationFrame(raf);
    raf=requestAnimationFrame(()=>{
      drawTrend(trendState.activeIndex);
      drawDonut();
    });
  };

  render();
  setupTrendHover();

  if('ResizeObserver'in window){
    const ro=new ResizeObserver(()=>{
      const tooltip=document.getElementById('salesTrendTooltip');
      tooltip?.classList.remove('is-visible');
      trendState.activeIndex=null;
      render();
    });

    ['salesTrendChart','paymentDonutChart'].forEach(id=>{
      const el=document.getElementById(id);
      if(el)ro.observe(el.parentElement||el);
    });
  }else{
    window.addEventListener('resize',()=>{
      trendState.activeIndex=null;
      render();
    });
  }
})();


/* V11 clean page transition / login loader. */
(() => {
  const loader = document.getElementById('mfpPageLoader');
  if (!loader) return;

  const loaderText = document.getElementById('mfpPageLoaderText');

  const setMessage = (message) => {
    if (loaderText && message) loaderText.textContent = message;
  };

  const showLoader = (message = 'Loading…') => {
    setMessage(message);
    loader.classList.add('is-active');
    loader.setAttribute('aria-hidden', 'false');
    document.documentElement.classList.add('mfp-loading');
  };

  const hideLoader = () => {
    loader.classList.remove('is-active');
    loader.setAttribute('aria-hidden', 'true');
    document.documentElement.classList.remove('mfp-loading');
  };

  window.MFPPageLoader = {
    show: showLoader,
    hide: hideLoader,
  };

  /* Important for Back/Forward Cache: never return to a page with the loader stuck. */
  window.addEventListener('pageshow', hideLoader);

  /* Show only for normal internal navigation. */
  document.addEventListener('click', (event) => {
    const link = event.target.closest('a[href]');
    if (!link) return;
    if (event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (link.target === '_blank' || link.hasAttribute('download')) return;
    if (link.dataset.noLoader === 'true') return;

    const rawHref = (link.getAttribute('href') || '').trim();
    if (
      !rawHref ||
      rawHref.startsWith('#') ||
      rawHref.startsWith('javascript:') ||
      rawHref.startsWith('mailto:') ||
      rawHref.startsWith('tel:')
    ) return;

    try {
      const url = new URL(link.href, window.location.href);
      if (url.origin !== window.location.origin) return;

      const sameDocument =
        url.pathname === window.location.pathname &&
        url.search === window.location.search &&
        Boolean(url.hash);

      if (sameDocument) return;
    } catch (_) {
      return;
    }

    showLoader(link.dataset.loadingMessage || 'Loading…');
  });

  /* Normal Django form submissions. */
  document.addEventListener('submit', (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (form.dataset.noLoader === 'true') return;
    if (!form.checkValidity()) return;

    const isLogin = form.classList.contains('login-form');
    const message = form.dataset.loadingMessage || (isLogin ? 'Signing in…' : 'Saving…');

    if (isLogin) {
      const button = form.querySelector('.login-submit');
      if (button) {
        button.disabled = true;
        button.classList.add('is-loading');
        button.textContent = button.dataset.loadingLabel || 'Signing in…';
      }
    }

    showLoader(message);
  });

  /*
   * Covers JavaScript redirects too, such as POS checkout redirecting to
   * the completed receipt after a successful AJAX request.
   */
  window.addEventListener('beforeunload', () => {
    if (!loader.classList.contains('is-active')) showLoader('Loading…');
  });
})();

/* V18 End Day live cash reconciliation + Stocktake live differences. */
document.addEventListener('DOMContentLoaded',()=>{
  const fmtMoney=v=>new Intl.NumberFormat('en-US',{maximumFractionDigits:0}).format(Number(v||0));
  const fmtQty=v=>{const n=Number(v||0);return Number.isInteger(n)?String(n):n.toFixed(3).replace(/0+$/,'').replace(/\.$/,'');};
  const number=v=>{const n=Number(String(v??'').replace(/,/g,''));return Number.isFinite(n)?n:0;};

  const closingForm=document.querySelector('.closing-form');
  if(closingForm){
    const base=number(closingForm.dataset.closingBaseCash);
    const opening=document.getElementById('closingOpeningFloat');
    const paidOut=document.getElementById('closingCashPaidOut');
    const counted=document.getElementById('closingCountedCash');
    const expectedEl=document.getElementById('closingExpected');
    const differenceEl=document.getElementById('closingDifference');
    const refresh=()=>{
      const expected=Math.max(0,base+number(opening?.value)-number(paidOut?.value));
      if(expectedEl)expectedEl.textContent=`${fmtMoney(expected)} TZS`;
      if(differenceEl){
        if(!counted?.value){
          differenceEl.textContent='—';
          differenceEl.classList.remove('negative');
        }else{
          const diff=number(counted.value)-expected;
          differenceEl.textContent=`${diff<0?'-':''}${fmtMoney(Math.abs(diff))} TZS`;
          differenceEl.classList.toggle('negative',diff<0);
        }
      }
    };
    [opening,paidOut,counted].forEach(input=>input?.addEventListener('input',refresh));
    refresh();
  }

  const stocktakeRows=[...document.querySelectorAll('[data-stocktake-row]')];
  const refreshStocktakeRow=row=>{
    const system=number(row.dataset.system);
    const baseUnit=row.dataset.baseUnit||'';
    const select=row.querySelector('.stocktake-unit-select');
    const input=row.querySelector('.stocktake-count-input');
    const diffEl=row.querySelector('[data-stocktake-diff]');
    if(!input||!diffEl)return;
    if(input.value===''){
      diffEl.textContent='—';
      diffEl.classList.remove('negative','positive');
      return;
    }
    const option=select?.options?.[select.selectedIndex];
    const conversion=Math.max(.000001,number(option?.dataset?.conversion||1));
    const counted=number(input.value)*conversion;
    const diff=counted-system;
    diffEl.textContent=`${diff>0?'+':''}${fmtQty(diff)} ${baseUnit}`;
    diffEl.classList.toggle('negative',diff<0);
    diffEl.classList.toggle('positive',diff>0);
  };

  stocktakeRows.forEach(row=>{
    row.querySelector('.stocktake-count-input')?.addEventListener('input',()=>refreshStocktakeRow(row));
    row.querySelector('.stocktake-unit-select')?.addEventListener('change',()=>refreshStocktakeRow(row));
    refreshStocktakeRow(row);
  });

  document.getElementById('fillSystemCounts')?.addEventListener('click',()=>{
    stocktakeRows.forEach(row=>{
      const input=row.querySelector('.stocktake-count-input');
      const select=row.querySelector('.stocktake-unit-select');
      if(!input)return;
      const option=select?.options?.[select.selectedIndex];
      const conversion=Math.max(.000001,number(option?.dataset?.conversion||1));
      input.value=fmtQty(number(row.dataset.system)/conversion);
      refreshStocktakeRow(row);
    });
  });
});

/* KILASI_V2_PHASE3B_BILLING — dynamic proforma lines, one global Novman app.js */
document.addEventListener('DOMContentLoaded', () => {
  const add = document.getElementById('kbAddLine');
  const rows = document.getElementById('kbLineRows');
  const template = document.getElementById('kbEmptyLineTemplate');
  const count = document.getElementById('id_lines-TOTAL_FORMS');
  if (!add || !rows || !template || !count) return;
  add.addEventListener('click', () => {
    const next = Number.parseInt(count.value, 10);
    if (!Number.isSafeInteger(next) || next < 0 || next > 499) return;
    const tbody = document.createElement('tbody');
    tbody.innerHTML = template.innerHTML.trim().replace(/__prefix__/g, String(next));
    const row = tbody.firstElementChild;
    if (row) { rows.appendChild(row); count.value = String(next + 1); }
  });
});
