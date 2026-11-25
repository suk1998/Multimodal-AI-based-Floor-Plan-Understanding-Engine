import type {NativeImage} from './api';
import type {DrawingRegion} from './observationAudit';

interface Props {
  image: NativeImage;
  regions: DrawingRegion[];
  selected?: string;
  busy: boolean;
  onSelect: (region?: DrawingRegion) => void;
  onClose: () => void;
}

function position([left, top, right, bottom]: DrawingRegion['bbox_native'], image: NativeImage) {
  const x = (left + right) / (2 * image.width);
  const y = (top + bottom) / (2 * image.height);
  const horizontal = x < .38 ? '왼쪽' : x > .62 ? '오른쪽' : '';
  const vertical = y < .38 ? '위' : y > .62 ? '아래' : '';
  return [horizontal, vertical].filter(Boolean).join(' ') || '페이지 중앙';
}

export function DrawingRegionPicker({image, regions, selected, busy, onSelect, onClose}: Props) {
  const parts = regions.filter(r => r.kind !== 'UNPARTITIONED_PAGE_CONTENT');
  const insidePage = parts.filter(r => {
    const [x, y, right, bottom] = r.bbox_native;
    return (right-x) * (bottom-y) < .8 * image.width * image.height;
  });
  const likelyPlans = insidePage.filter(r => r.kind === 'FLOORPLAN_CANDIDATE');
  const visible = likelyPlans.length ? likelyPlans : insidePage.slice(0,4);
  const secondary = parts.filter(r => !visible.includes(r));

  function card(region: DrawingRegion) {
    const [x,y,right,bottom] = region.bbox_native;
    return <button type="button" key={region.region_id} data-region-choice={region.region_id}
      className="drawing-region-card" aria-pressed={selected===region.region_id} disabled={busy}
      onClick={()=>onSelect(region)}>
      <svg viewBox={`${x} ${y} ${right-x} ${bottom-y}`} aria-hidden="true">
        <image href={image.url} width={image.width} height={image.height}/>
      </svg>
      <span>{position(region.bbox_native,image)}</span>
    </button>;
  }

  return <details className="drawing-region-picker" onToggle={e=>{if(!e.currentTarget.open)onClose();}}>
    <summary>페이지 안에서 분석할 도면 선택</summary>
    <p>이 도면을 따로 분석할 때 쓰는 설정입니다. 미리보기를 고르면 위 도면에서 위치를 확인할 수 있습니다.</p>
    <button type="button" aria-pressed={!selected} disabled={busy} onClick={()=>onSelect()}>
      페이지 전체 사용
    </button>
    {visible.length>0&&<div className="drawing-region-cards">{visible.map(card)}</div>}
    {secondary.length>0&&<details className="drawing-region-secondary">
      <summary>그 밖의 영역 미리보기</summary>
      <div className="drawing-region-cards">{secondary.map(card)}</div>
    </details>}
  </details>;
}
