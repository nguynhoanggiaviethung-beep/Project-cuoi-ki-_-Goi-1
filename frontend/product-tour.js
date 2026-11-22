(() => {
  const steps = [
    {route:"overview",selector:"#mode-badge",title:"Trạng thái dữ liệu",copy:"Kiểm tra nguồn SSI, kết nối và độ mới. Chỉ khi hệ thống nhận message mới và xác nhận đã lưu mới được đánh dấu LIVE; ngoài phiên sẽ hiển thị dữ liệu đã lưu gần nhất."},
    {route:"overview",selector:"#view-overview .filter-bar",title:"Chọn mã và khoảng quan sát",copy:"Chọn một mã hoặc toàn thị trường, rồi chọn cửa sổ biểu đồ. Bộ lọc này giúp bạn xem dữ liệu trong đúng phạm vi cần phân tích."},
    {route:"overview",selector:"#view-overview .kpi-grid",title:"Đọc các chỉ số chính",copy:"Các thẻ tóm tắt số mã có snapshot, spread, độ sâu sổ lệnh và mất cân bằng mua/bán. Dấu gạch ngang nghĩa là chưa có dữ liệu hợp lệ cho chỉ số đó."},
    {route:"overview",selector:"#view-overview .market-liquidity-panel",title:"Theo dõi giá trị giao dịch",copy:"Biểu đồ so sánh giá trị khớp lũy kế trong phiên với các phiên tham chiếu cùng thời điểm. Chọn HOSE, HNX hoặc UPCoM để đổi sàn."},
    {route:"overview",selector:"#view-overview .depth-overview-panel .panel-heading",title:"Quan sát độ sâu sổ lệnh",copy:"Phía trên đường 0 là giá trị niêm yết bên mua; phía dưới là bên bán. Đây là lượng đang chờ trong các mức giá được cung cấp, không đảm bảo sẽ khớp."},
    {route:"overview",selector:"#view-overview #overview-ranking",title:"Tìm mã để xem tiếp",copy:"Bảng xếp hạng lọc mã theo spread trong cửa sổ đang chọn. Mở Xem tất cả để đổi tiêu chí, sàn, số dòng hoặc tìm mã cụ thể."},
    {route:"watchlist",selector:"#view-watchlist .watchlist-panel-heading",title:"Tạo danh mục theo dõi",copy:"Thêm mã bạn quan tâm để so spread, độ sâu, lịch sử và chi phí mô phỏng. Ghi chú và danh sách được lưu trong hồ sơ cục bộ trên máy này."},
    {route:"methodology",selector:"#view-methodology .methodology-collector .panel-heading",title:"Kiểm chứng nguồn và phương pháp",copy:"Tại đây xem trạng thái collector, message nhận được, dữ liệu đã ghi và các bước tính chỉ số. Socket connected riêng lẻ chưa đủ để xác nhận đang có dữ liệu mới."}
  ];
  const root=document.querySelector("#product-tour");
  if(!root)return;
  const card=root.querySelector(".product-tour-card"),title=root.querySelector("#product-tour-title"),copy=root.querySelector("#product-tour-copy"),count=root.querySelector("#product-tour-count");
  const back=root.querySelector("#product-tour-back"),next=root.querySelector("#product-tour-next"),skip=root.querySelector("#product-tour-skip");
  const shades=Object.fromEntries([...root.querySelectorAll("[data-tour-shade]")].map(node=>[node.dataset.tourShade,node]));
  let index=0,target=null,originRoute="overview",layoutFrame=0;
  const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  const clamp=(value,min,max)=>Math.max(min,Math.min(max,value));

  function layout(){
    cancelAnimationFrame(layoutFrame);
    layoutFrame=requestAnimationFrame(()=>{
      const pad=9,w=window.innerWidth,h=window.innerHeight;
      if(!target||!target.isConnected){
        shades.top.style.cssText=`left:0;top:0;width:${w}px;height:${h}px`;
        shades.right.style.cssText="left:0;top:0;width:0;height:0";
        shades.bottom.style.cssText="left:0;top:0;width:0;height:0";
        shades.left.style.cssText="left:0;top:0;width:0;height:0";
        card.style.left="50%";card.style.top="50%";card.style.transform="translate(-50%,-50%)";
        return;
      }
      const rect=target.getBoundingClientRect(),x=clamp(rect.left-pad,0,w),y=clamp(rect.top-pad,0,h),right=clamp(rect.right+pad,0,w),bottom=clamp(rect.bottom+pad,0,h);
      shades.top.style.cssText=`left:0;top:0;width:${w}px;height:${y}px`;
      shades.left.style.cssText=`left:0;top:${y}px;width:${x}px;height:${Math.max(0,bottom-y)}px`;
      shades.right.style.cssText=`left:${right}px;top:${y}px;width:${Math.max(0,w-right)}px;height:${Math.max(0,bottom-y)}px`;
      shades.bottom.style.cssText=`left:0;top:${bottom}px;width:${w}px;height:${Math.max(0,h-bottom)}px`;
      const cw=card.offsetWidth,ch=card.offsetHeight,gap=18;
      let left=rect.left+(rect.width-cw)/2,top=rect.bottom+gap,transform="none";
      if(top+ch>h-14)top=rect.top-ch-gap;
      if(top<14){top=clamp(rect.bottom+gap,14,h-ch-14);}
      left=clamp(left,14,w-cw-14);
      card.style.left=`${left}px`;card.style.top=`${top}px`;card.style.transform=transform;
    });
  }

  async function showStep(nextIndex){
    index=clamp(nextIndex,0,steps.length-1);
    const step=steps[index];
    if(window.sliRoute!==step.route&&typeof window.setRoute==="function"){
      const loading=window.setRoute(step.route);
      await Promise.race([Promise.resolve(loading).catch(()=>{}),wait(9500)]);
      await wait(220);
    }
    target?.classList.remove("tour-target-active");
    target=document.querySelector(step.selector);
    title.textContent=step.title;copy.textContent=step.copy;count.textContent=`Bước ${index+1}/${steps.length}`;
    back.hidden=index===0;
    next.textContent=index===steps.length-1?"Hoàn tất ✓":"Tiếp →";
    if(target){target.scrollIntoView({behavior:"smooth",block:"center",inline:"nearest"});target.classList.add("tour-target-active");}
    root.hidden=false;
    await wait(280);
    layout();next.focus({preventScroll:true});
  }

  function finish(skipped=false){
    target?.classList.remove("tour-target-active");target=null;
    root.hidden=true;window.productTourActive=false;window.productTourPending=false;
    try{localStorage.setItem("sli-product-tour-done","1");}catch(_){}
    if(window.sliRoute!==originRoute&&typeof window.setRoute==="function")window.setRoute(originRoute);
    window.dispatchEvent(new CustomEvent("sli-product-tour-finished",{detail:{skipped}}));
  }

  async function start(options={}){
    if(window.productTourActive)finish(true);
    if(!options.force&&localStorage.getItem("sli-product-tour-done")==="1")return;
    originRoute=window.sliRoute||"overview";
    window.productTourPending=true;window.productTourActive=true;
    root.hidden=false;
    await showStep(0);
  }

  window.startProductTour=start;
  next.addEventListener("click",()=>index===steps.length-1?finish(false):showStep(index+1));
  back.addEventListener("click",()=>showStep(index-1));
  skip.addEventListener("click",()=>finish(true));
  root.querySelectorAll(".tour-shade").forEach(node=>node.addEventListener("click",event=>event.preventDefault()));
  window.addEventListener("resize",layout,{passive:true});
  window.addEventListener("scroll",layout,{passive:true,capture:true});
  document.addEventListener("keydown",event=>{
    if(!window.productTourActive)return;
    if(event.key==="Escape"){event.preventDefault();finish(true);}
    else if(event.key==="ArrowRight"){event.preventDefault();index===steps.length-1?finish(false):showStep(index+1);}
    else if(event.key==="ArrowLeft"&&index>0){event.preventDefault();showStep(index-1);}
  });
})();
