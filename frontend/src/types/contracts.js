/** @typedef {{id:number,code:string,route:string,zone:string,driver:string}} Shipment */
/** @typedef {{id:number,state:'queued'|'running'|'done'|'failed',result:object}} Study */
/**
 * @typedef {{zone:string,total_epsilon:number,spent_epsilon:number,remaining_epsilon:number}} PrivacyBudget
 * @typedef {{release_id:number,zone:string,state:'draft'|'published'|'failed',
 *   bin_version:string,bins:object,result:object,epsilon_spent:number,
 *   created:string,published:string|null}} PrivacyRelease
 * 对外发布仅含匿名分箱频数，绝无班次/样本明细字段。
 * @typedef {{id:number,zone:string,release_id:number,kind:'charge'|'refund',
 *   epsilon:number,balance_after:number,actor:string,note:string,created:string}} PrivacyLedger
 */
export {};
