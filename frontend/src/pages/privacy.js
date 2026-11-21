import {state} from '../stores/session.js';
import {esc} from '../utils/html.js';
import {badge} from '../components/common/status.js';
import {field,select} from '../components/common/fields.js';
import {table} from '../components/common/table.js';
import {ZONES} from '../constants/enums.js';

const isAdmin=()=>state.user.role==='admin';
const canWrite=()=>state.user.role!=='viewer';

function budgetCards(){
  return `<div class=metrics>${state.budgets.map(b=>
    `<div><strong>${Number(b.remaining_epsilon).toFixed(2)}</strong>${esc(b.zone)} 剩余 ε<div class=eyebrow>已用 ${Number(b.spent_epsilon).toFixed(2)} / 总额 ${Number(b.total_epsilon).toFixed(2)}</div></div>`
  ).join('')}</div>`;
}

function histogramBlock(m){
  const max=Math.max(1,...m.noisy_counts);
  return `<article class=rec><h4>${m.metric==='exposure'?'温控暴露分布（分钟）':'货损分布（%）'} <span class=eyebrow>ε/分布=${Number(m.epsilon).toFixed(3)} · 噪声尺度=${Number(m.laplace_scale).toFixed(3)}</span></h4>
  <div class=scroll><table><thead><tr>${m.bins.map(b=>`<th>${esc(b)}</th>`).join('')}</tr></thead>
  <tbody><tr>${m.noisy_counts.map(c=>`<td><strong>${c}</strong><div style="height:${Math.round(c/max*46)+2}px;background:#a65437;opacity:.7;border-radius:2px;margin-top:4px"></div></td>`).join('')}</tr></tbody></table></div>
  <p class=eyebrow>裁剪 [${m.clip_min}, ${m.clip_max}] ${esc(m.unit)} · 频数已叠加拉普拉斯噪声并取非负整数</p></article>`;
}

export function render(){
  const sel=state.selectedRelease;
  return `<div class=split><section class=panel>
    <h2>对外匿名统计（仅温控与货损分布）</h2>
    <p class=notice>所有发布共用冻结的分箱边界与裁剪范围，按温区聚合样本频数并叠加拉普拉斯噪声。<strong>对外文件与接口不含任何班次号、样本号、线路或驾驶员明细</strong>；内部原始研究仍可在“关联分析”查看。每个温区共用一个累计隐私预算，重叠研究也占用同一额度，余额不足将拒绝发布。</p>
    ${budgetCards()}
    ${sel?`<h3>发布单 #${sel.release_id} · ${esc(sel.zone)} ${badge(sel.state)}</h3>
      ${sel.state==='published'?`${sel.result.metrics.map(histogramBlock).join('')}
      <p class=notice>${esc(sel.result.note)}</p>
      <p>本次共消耗 ε=${Number(sel.epsilon_spent).toFixed(4)} · 口径版本 ${esc(sel.bin_version)} · ${esc(sel.published||'')}</p>
      <button id=priv-download data-id=${sel.release_id}>下载对外匿名 JSON</button>`
      :`<p class=notice>${esc('发布未成功或未对外可见')}</p>`}`
      :'<p class=notice>在右侧申请一次匿名发布，成功后在此查看分布并下载。</p>'}
    <h3>发布记录</h3>
    ${state.releases.map(r=>`<button class=study data-release=${r.release_id}>#${r.release_id} ${esc(r.zone)} · ε=${Number(r.epsilon_spent).toFixed(2)} · ${esc(r.state)}</button>`).join('')||'<p class=notice>尚无发布。</p>'}
    ${isAdmin()?`<h3>预算流水（查账）</h3>${table(['温区','类型','ε','余额','操作人','时间'],state.ledger.map(l=>[l.zone,l.kind==='charge'?'扣费':'退款',Number(l.epsilon).toFixed(4),Number(l.balance_after).toFixed(4),l.actor,(l.created||'').replace('T',' ').slice(0,19)]))}`:''}
  </section>
  <aside class=side>
    <h2>申请匿名发布</h2>
    <form id=release ${canWrite()?'':'disabled'}>
      ${select('温区','zone',ZONES.map(z=>[z,z]))}
      ${field('本次预算 epsilon','epsilon','number','min=0.01 max=10 step=0.01 value=0.5 required')}
      <p>两个分布（温控、货损）平分本次 ε；同一申请重试请复用同一幂等键，系统返回原结果且不重复扣费、不重新加噪。</p>
      <button class=primary ${canWrite()?'':'disabled'}>申请发布并扣费</button>
    </form>
    ${isAdmin()?`<h2>设置温区总预算</h2>
      <form id=budget>
        ${select('温区','zone',ZONES.map(z=>[z,z]))}
        ${field('总预算 epsilon','total_epsilon','number','min=0.01 max=1000 step=0.1 required')}
        <p>只能调整到不低于该温区已消耗的额度。</p>
        <button class=primary>保存预算</button>
      </form>`:'<p class=notice>仅负责人（admin）可设置总预算与查看完整流水。</p>'}
  </aside></div>`;
}
