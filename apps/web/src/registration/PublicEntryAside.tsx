/** Presentation only: shared by public flows without owning form or session state. */
export function PublicEntryAside() {
  return (
    <aside className="publicEntryAside" aria-label="竞学 ERP 账号服务">
      <div className="publicEntryBrand"><span aria-hidden="true">竞</span><strong>竞学 ERP<small>教育运营 · 协同有序</small></strong></div>
      <div className="publicEntryStory">
        <p>从这里，连接每一天的成长。</p>
        <h2>有序开始，<br />安心前行。</h2>
        <span>按步骤完成账号与资料验证，<br />让协作从清晰的信息开始。</span>
      </div>
      <ol className="publicEntryJourney" aria-label="账号服务流程说明">
        <li><span>01</span><div><strong>验证联系方式</strong><small>确认由你本人操作</small></div></li>
        <li><span>02</span><div><strong>完善个人资料</strong><small>按所选身份填写信息</small></div></li>
        <li><span>03</span><div><strong>进入协同工作台</strong><small>可用功能以账号权限为准</small></div></li>
      </ol>
      <p className="publicEntryPrivacy">实名资料保护 · 角色权限隔离</p>
    </aside>
  )
}
