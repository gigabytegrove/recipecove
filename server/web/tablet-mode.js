'use strict';
/* RecipeCove Tablet Mode: local cooking progress only; recipes remain unchanged. */
(function () {
  const storagePrefix = 'recipecove-cooking-v1:';
  const substitutionRules = [
    {find:/\bbuttermilk\b/i, ideas:[
      'For each cup: put 1 tablespoon lemon juice or white vinegar in a measuring cup, then add milk to the 1-cup line. Rest for 5–10 minutes.',
      'Plain unsweetened yogurt thinned with a little milk may work in some recipes.'
    ]},
    {find:/\bbaking powder\b/i, ideas:[
      'For 1 teaspoon baking powder: mix 1/4 teaspoon baking soda and 1/2 teaspoon cream of tartar. Use promptly.'
    ]},
    {find:/\bsour cream\b/i, ideas:[
      'Plain full-fat Greek yogurt often works in equal volume; flavor and texture may differ.'
    ]},
    {find:/\bheavy (?:whipping )?cream\b/i, ideas:[
      'For soups or sauces, half-and-half can work, but will be thinner. It cannot reliably replace whipping cream for whipped peaks.'
    ]},
    {find:/\b(?:plain |greek )?yogurt\b/i, ideas:[
      'Sour cream can work for dips, dressings, and many baked goods. Adjust acidity and consistency to taste.'
    ]},
    {find:/\b(?:unsalted |salted )?butter\b/i, ideas:[
      'For sautéing, use a neutral oil or olive oil. In baking, amounts and results depend on the recipe; do not assume 1:1.'
    ]},
    {find:/\b(?:whole |skim |2% |low.fat )?milk\b/i, ideas:[
      'Unsweetened oat or soy milk can replace dairy milk in many recipes at equal volume. Check allergens and flavor.',
      'For savory recipes, half-and-half diluted with water may work; the result will be richer.'
    ]},
    {find:/\b(?:large |medium )?eggs?\b/i, ideas:[
      'For binding in some muffins or quick breads: 1 tablespoon ground flaxseed plus 3 tablespoons water, rested 10 minutes, per egg. Not suitable for every recipe.',
      'For some moist baked goods: 1/4 cup unsweetened applesauce per egg; texture and rising can change.'
    ]},
    {find:/\bbrown sugar\b/i, ideas:[
      'For 1 cup light brown sugar: combine 1 cup granulated sugar with 1 tablespoon molasses. Mix thoroughly.'
    ]},
    {find:/\bpowdered sugar\b|\bconfectioners.? sugar\b/i, ideas:[
      'For many frostings: finely process 1 cup granulated sugar with 1 tablespoon cornstarch. It may remain grainier than commercial powdered sugar.'
    ]},
    {find:/\b(?:white |granulated )sugar\b/i, ideas:[
      'Honey or maple syrup may work in some recipes, but changes moisture, sweetness and baking behavior; adjust the recipe rather than swapping blindly.'
    ]},
    {find:/\b(?:all.purpose |plain )flour\b/i, ideas:[
      'Bread flour may work in some doughs but develops a chewier texture. Gluten-free blends need recipe-specific adjustments.'
    ]},
    {find:/\bfresh (?:lemon|lime) juice\b|\blemon juice\b|\blime juice\b/i, ideas:[
      'An equal amount of bottled lemon or lime juice can work where fresh aroma is not essential; taste and adjust.'
    ]},
    {find:/\blemon zest\b|\blime zest\b/i, ideas:[
      'A little citrus extract can provide aroma. Do not substitute juice at equal volume; it adds liquid and acidity.'
    ]},
    {find:/\b(?:honey|maple syrup)\b/i, ideas:[
      'Honey and maple syrup can sometimes replace one another by volume in sauces or dressings; expect different flavors.'
    ]},
    {find:/\b(?:fresh |minced )?garlic\b/i, ideas:[
      'For each medium garlic clove, start with 1/8 teaspoon garlic powder, then adjust to taste.'
    ]},
    {find:/\b(?:fresh |chopped )?onion\b/i, ideas:[
      'For savory dishes, 1 tablespoon onion powder can replace roughly 1 medium onion for flavor, but not its bulk or moisture.'
    ]},
    {find:/\btomato paste\b/i, ideas:[
      'Tomato sauce can provide tomato flavor, but is less concentrated and adds liquid. Reduce the sauce if consistency matters.'
    ]},
    {find:/\b(?:chicken|vegetable|beef) (?:broth|stock)\b/i, ideas:[
      'A compatible broth or stock can often replace another; season carefully. Water with appropriate seasonings may work in some dishes.'
    ]},
    {find:/\b(?:cornstarch|corn flour)\b/i, ideas:[
      'For thickening hot sauces, all-purpose flour may work but usually needs more quantity and cooking time. Not equivalent in every recipe.'
    ]},
    {find:/\bfresh (?:basil|parsley|thyme|oregano|rosemary)\b/i, ideas:[
      'Dried herbs are more concentrated. Start with about 1 teaspoon dried for each tablespoon fresh and adjust to taste.'
    ]}
  ];
  let active = null;
  let sentinel = null;
  let visibilityHandler = null;

  function element(tag, className, text) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined) el.textContent = String(text);
    return el;
  }
  function button(label, className, handler) {
    const el = element('button', className, label);
    el.type = 'button';
    el.addEventListener('click', handler);
    return el;
  }
  function ingredientText(input) {
    if (typeof input === 'string') return input;
    if (!input || typeof input !== 'object') return '';
    return String(input.raw || [input.quantity, input.unit, input.name].filter(Boolean).join(' ') || input.name || '');
  }
  function stepText(input) {
    return typeof input === 'string' ? input : String(input && (input.text || input.instruction) || '');
  }
  function suggestions(ingredient) {
    const rule = substitutionRules.find(entry => entry.find.test(ingredient));
    return rule ? rule.ideas : [];
  }
  function storageKey(recipeId, namespace) {
    return storagePrefix + String(namespace) + ':' + String(recipeId);
  }
  function readProgress(key) {
    try {
      const value = JSON.parse(localStorage.getItem(key) || 'null');
      if (value && value.version === 1 && Array.isArray(value.ingredients) &&
          Array.isArray(value.steps) && value.substitutions && typeof value.substitutions === 'object') {
        return value;
      }
    } catch (_) { /* Private browsing may reject storage. Cooking still works in memory. */ }
    return {version:1, ingredients:[], steps:[], substitutions:{}};
  }
  function saveProgress(ctx) {
    try { localStorage.setItem(ctx.key, JSON.stringify(ctx.progress)); }
    catch (_) { ctx.storageNotice.hidden = false; }
  }
  function removeProgress(ctx) {
    try { localStorage.removeItem(ctx.key); } catch (_) { /* best effort */ }
  }
  function setCount(ctx) {
    const total = ctx.ingredients.length + ctx.steps.length;
    const done = ctx.progress.ingredients.length + ctx.progress.steps.length;
    ctx.progressBar.max = Math.max(1, total);
    ctx.progressBar.value = done;
    ctx.progressLabel.textContent = total ? (done + ' of ' + total + ' completed') : 'Nothing to check off yet';
    ctx.finish.disabled = ctx.working;
  }
  function toggle(ctx, collection, index, checked, row) {
    const values = new Set(ctx.progress[collection]);
    if (checked) values.add(index); else values.delete(index);
    ctx.progress[collection] = [...values].sort((a,b) => a-b);
    row.classList.toggle('checked', checked);
    saveProgress(ctx);
    setCount(ctx);
  }
  async function acquireWake(ctx) {
    if (active !== ctx) return;
    if (!('wakeLock' in navigator) || !navigator.wakeLock ||
        typeof navigator.wakeLock.request !== 'function') {
      ctx.wake.textContent = 'Screen stays on: unavailable in this browser. On iPad Air 2, use Settings → Display & Brightness → Auto-Lock → Never while cooking.';
      ctx.wake.classList.add('tablet-wake-warning');
      return;
    }
    if (document.visibilityState !== 'visible') return;
    if (sentinel && !sentinel.released) return;
    try {
      const lock = await navigator.wakeLock.request('screen');
      if (active !== ctx) { await lock.release(); return; }
      sentinel = lock;
      ctx.wake.textContent = 'Screen awake while Tablet Mode is open';
      ctx.wake.classList.remove('tablet-wake-warning');
      lock.addEventListener('release', () => {
        if (sentinel === lock) sentinel = null;
        if (active === ctx) {
          ctx.wake.textContent = 'Screen awake lock paused. Tap here to retry.';
          ctx.wake.classList.add('tablet-wake-warning');
        }
      });
    } catch (_) {
      ctx.wake.textContent = 'Could not keep the screen awake. Check battery-saving settings or set Auto-Lock to Never.';
      ctx.wake.classList.add('tablet-wake-warning');
    }
  }
  async function leave() {
    active = null;
    document.body.classList.remove('tablet-mode-active');
    if (visibilityHandler) document.removeEventListener('visibilitychange', visibilityHandler);
    visibilityHandler = null;
    const current = sentinel;
    sentinel = null;
    if (current && !current.released) try { await current.release(); } catch (_) {}
  }
  async function open(recipe, options) {
    await leave();
    const id = Number(recipe.id);
    if (!Number.isSafeInteger(id) || id < 1) throw Error('Invalid recipe for Tablet Mode');
    const ingredients = (Array.isArray(recipe.ingredients) ? recipe.ingredients : []).map(ingredientText);
    const steps = (Array.isArray(recipe.instructions) ? recipe.instructions : []).map(stepText);
    const key = storageKey(id, options.namespace);
    const progress = readProgress(key);
    // Prevent stale checked indexes when a recipe's instructions have changed.
    const fingerprint = JSON.stringify([ingredients, steps]);
    if (progress.fingerprint && progress.fingerprint !== fingerprint) {
      progress.ingredients = []; progress.steps = []; progress.substitutions = {};
    }
    progress.fingerprint = fingerprint;
    progress.ingredients = progress.ingredients.filter(i => Number.isInteger(i) && i >= 0 && i < ingredients.length);
    progress.steps = progress.steps.filter(i => Number.isInteger(i) && i >= 0 && i < steps.length);
    const ctx = {key, progress, ingredients, steps, working:false};
    active = ctx;
    document.body.classList.add('tablet-mode-active');
    const root = options.root;
    const view = element('section','tablet-workspace');
    const top = element('header','tablet-header');
    const title = element('div','tablet-headline');
    title.append(element('p','tablet-eyebrow','RECIPECOVE • TABLET MODE'),element('h1','',recipe.title||'Cooking'));
    if (recipe.servings) title.append(element('p','tablet-servings','Servings: '+recipe.servings));
    const exit = button('← Recipe', 'tablet-exit', async () => {await options.back();});
    top.append(title,exit);
    const status = element('div','tablet-status');
    const progressLabel = element('span','tablet-progress-label');
    const progressBar = element('progress','tablet-progress');
    progressBar.setAttribute('aria-label','Cooking progress');
    const wake = element('button','tablet-wake','Checking screen awake support…');
    wake.type = 'button';
    wake.addEventListener('click', () => acquireWake(ctx));
    const storageNotice = element('p','tablet-storage-warning','Progress cannot be saved on this device. Keep this page open.');
    storageNotice.hidden = true;
    status.append(progressLabel,progressBar,wake,storageNotice);
    const grid = element('div','tablet-grid');
    const ingredientPanel = element('section','tablet-panel');
    ingredientPanel.append(element('h2','','Ingredients'),element('p','tablet-hint','Check each ingredient as you prepare it. Tap Substitutions if you are missing something.'));
    const ingredientList = element('div','tablet-check-list');
    ingredients.forEach((name,index) => {
      const row = element('div','tablet-item');
      const label = element('label','tablet-check');
      const check = element('input');
      check.type = 'checkbox';
      check.checked = progress.ingredients.includes(index);
      row.classList.toggle('checked',check.checked);
      check.addEventListener('change',() => toggle(ctx,'ingredients',index,check.checked,row));
      const text = element('span','',name || 'Unnamed ingredient');
      label.append(check,text);
      row.append(label);
      const ideas = suggestions(name);
      const sub = element('details','tablet-substitution');
      const summary = element('summary','','Substitutions');
      sub.append(summary);
      const examples = element('div','tablet-substitution-body');
      if (ideas.length) {
        ideas.forEach(idea => examples.append(element('p','',idea)));
      } else {
        examples.append(element('p','','No verified general substitution available. Check the recipe or use a substitution you already know is suitable.'));
      }
      examples.append(element('p','tablet-caution','Confirm allergies, dietary requirements, proportions, and whether the swap works for this cooking method.'));
      const custom = element('input','tablet-substitution-input');
      custom.type = 'text';
      custom.maxLength = 180;
      custom.placeholder = 'My replacement (optional)';
      custom.setAttribute('aria-label','Replacement for '+name);
      custom.value = String(progress.substitutions[index] || '');
      custom.addEventListener('input',()=>{
        const value = custom.value.trim();
        if(value)progress.substitutions[index]=value;else delete progress.substitutions[index];
        saveProgress(ctx);
      });
      examples.append(custom);
      sub.append(examples);
      row.append(sub);
      ingredientList.append(row);
    });
    if (!ingredients.length) ingredientList.append(element('p','tablet-empty','No ingredients were provided for this recipe.'));
    ingredientPanel.append(ingredientList);
    const stepPanel = element('section','tablet-panel');
    stepPanel.append(element('h2','','Cooking steps'),element('p','tablet-hint','Work through the instructions and check them off one by one.'));
    const stepList = element('div','tablet-check-list');
    steps.forEach((instruction,index)=>{
      const row = element('label','tablet-step tablet-item');
      const check = element('input');
      check.type = 'checkbox';
      check.checked = progress.steps.includes(index);
      row.classList.toggle('checked',check.checked);
      check.addEventListener('change',()=>toggle(ctx,'steps',index,check.checked,row));
      row.append(check,element('span','tablet-step-number',String(index+1)),element('span','tablet-step-text',instruction||'Step '+(index+1)));
      stepList.append(row);
    });
    if (!steps.length) stepList.append(element('p','tablet-empty','No cooking steps were provided for this recipe.'));
    stepPanel.append(stepList);
    grid.append(ingredientPanel,stepPanel);
    const footer = element('footer','tablet-footer');
    const reset = button('Reset checklist','tablet-reset',()=>{
      if (!window.confirm('Clear checked items and substitutions for this cooking session?')) return;
      progress.ingredients=[];progress.steps=[];progress.substitutions={};
      saveProgress(ctx);
      open(recipe,options);
    });
    const finish = button('Finish cooking','tablet-finish',async()=>{
      if (ctx.working) return;
      if (!window.confirm('Finish cooking? This will mark the recipe as made and clear your checklist.')) return;
      ctx.working = true;setCount(ctx);
      try {
        await options.finish(id);
        removeProgress(ctx);
        await options.back();
      } catch (error) {
        ctx.working = false;setCount(ctx);
        options.error(error instanceof Error ? error.message : 'Unable to finish cooking');
      }
    });
    footer.append(reset,finish);
    view.append(top,status,grid,footer);
    root.replaceChildren(view);
    Object.assign(ctx,{wake,storageNotice,progressLabel,progressBar,finish});
    saveProgress(ctx);
    setCount(ctx);
    visibilityHandler = () => { if (document.visibilityState === 'visible') acquireWake(ctx); };
    document.addEventListener('visibilitychange',visibilityHandler);
    await acquireWake(ctx);
  }
  window.RecipeCoveTablet = {open,leave,ingredientText,stepText,suggestions};
})();
