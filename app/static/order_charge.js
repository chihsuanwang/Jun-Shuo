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

  // 銷項稅額是 0 的提醒(2026-11-07):只填了發票號碼、忘了填稅額也會被放行。不擋(未加工的鮮果等免稅商品稅額本來就是 0),
  // 跳確認視窗並附 5% 估算,按取消回去補。要在頁面其他送出檢查(客戶、成本、產品線)之後才註冊,前面擋下了就不再跳。
  function wireTaxReminder(form) {
    if (!form) return;
    form.addEventListener('submit', function (e) {
      if (e.defaultPrevented) return;
      var kind = form.querySelector('select[name="order_kind"]');
      if (kind && kind.value !== '銷售') return;
      var tax = form.querySelector('input[name="sales_tax"]');
      if (!tax || (parseFloat(tax.value) || 0) !== 0) return;
      var tEl = document.getElementById('ordertotal');
      var total = tEl ? parseFloat(tEl.textContent.replace(/,/g, '')) || 0 : 0;
      if (total <= 0) return;
      var est = Math.round(total * 5 / 105);
      var msg = '這張銷售訂單的「銷項稅額」是 0。\n'
        + '依 5% 估算約 ' + est.toLocaleString() + ' 元(應收合計 ' + total.toLocaleString() + ' ÷ 1.05 × 5%)。\n\n'
        + '免稅商品(例如未加工的鮮果)稅額本來就是 0,按「確定」照樣存檔;\n忘了填就按「取消」回去補。';
      if (!confirm(msg)) {
        e.preventDefault();
        tax.focus();
        if (tax.select) tax.select();
      }
    });
  }

  return { r2: r2, apply: apply, wire: wire, wireTaxReminder: wireTaxReminder };
})();
