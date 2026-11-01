/* 訂單「本次實際收費」(2026-11-01 同仁回饋;新增訂單 / 訂單明細兩頁共用,code-review 後抽出來)
   - 沒動過就跟著商品小計;改過就照填的(data-touched),跟小計的差額 = 整單折扣(總帳 4119-01),
     收得比小計多就紅字提醒(不擋);清空 = 回到跟著小計。
   - data-disc:已存的訂單 / 複製的訂單原本的折扣,打開時帶回「小計 − 折扣」。
   - 訂單種類不是「銷售」時整列藏起來,實際收費 = 小計。
   用法:recalc() 裡 var charged = GYCharge.apply(sum);頁面載入時 GYCharge.wire(recalc)。 */
window.GYCharge = (function () {
  function r2(v) { return Math.round(v * 100) / 100; }

  function apply(sum) {
    var ac = document.getElementById('actualCharge'), hint = document.getElementById('chargeHint'),
        kindSel = document.querySelector('select[name="order_kind"]'),
        sale = !kindSel || kindSel.value === '銷售', charged = sum;
    if (!ac) return charged;
    document.getElementById('chargeRow').hidden = !sale;
    if (ac.dataset.disc !== undefined && !ac.dataset.init) {
      ac.dataset.init = '1';
      if (parseFloat(ac.dataset.disc)) { ac.value = r2(sum - parseFloat(ac.dataset.disc)); ac.dataset.touched = '1'; }
    }
    if (!ac.dataset.touched) ac.value = r2(sum);
    if (sale && ac.value !== '') charged = parseFloat(ac.value) || 0;
    var d = r2(charged - sum);
    hint.textContent = !sale || !d ? '' : d < 0 ? '折扣 ' + d.toLocaleString() : '比小計高 +' + d.toLocaleString() + ',請確認';
    hint.style.color = d > 0 ? 'var(--seal)' : '';
    hint.style.fontWeight = d > 0 ? '600' : '';
    return charged;
  }

  function wire(recalc) {
    var ac = document.getElementById('actualCharge'), kind = document.querySelector('select[name="order_kind"]');
    if (ac) ac.addEventListener('input', function () {
      if (ac.value === '') delete ac.dataset.touched; else ac.dataset.touched = '1';
      recalc();
    });
    if (kind) kind.addEventListener('change', recalc);
  }

  return { r2: r2, apply: apply, wire: wire };
})();
