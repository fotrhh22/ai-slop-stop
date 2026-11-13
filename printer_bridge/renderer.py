"""384-dot PT-210 receipt renderer. Photos use Atkinson; text stays crisp."""
from __future__ import annotations
from io import BytesIO
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import os
import re
from PIL import Image, ImageDraw, ImageFont

WIDTH = 384
CONTENT = 360
PHOTO_WIDTH = 232
QR_PATH = Path(__file__).with_name('assets') / 'receipt-qr.png'

def resolve_font():
    choices = [os.getenv('PRINT_FONT_PATH',''),
        '/System/Library/Fonts/Supplemental/AppleGothic.ttf',
        '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc']
    for name in choices:
        if name and Path(name).is_file(): return name
    raise RuntimeError('KOREAN_FONT_NOT_FOUND: set PRINT_FONT_PATH')

def atkinson(image):
    """Same luminance, threshold and six 1/8 taps as web/src/utils/thermal.ts."""
    rgba=image.convert('RGBA')
    w,h=rgba.size
    values=[]
    for r,g,b,a in rgba.getdata():
        alpha=a/255
        values.append((.2126*r+.7152*g+.0722*b)*alpha+255*(1-alpha))
    out=Image.new('1',(w,h),1)
    px=out.load()
    for y in range(h):
        for x in range(w):
            i=y*w+x
            v=255 if values[i]>=127.5 else 0
            px[x,y]=255 if v else 0
            error=(values[i]-v)/8
            for dx,dy in ((1,0),(2,0),(-1,1),(0,1),(1,1),(0,2)):
                nx,ny=x+dx,y+dy
                if 0<=nx<w and ny<h: values[ny*w+nx]+=error
    return out

class Layout:
    def __init__(self,font_path):
        self.font_path=font_path
        self.image=Image.new('L',(WIDTH,12000),255)
        self.draw=ImageDraw.Draw(self.image)
        self.y=16
    def font(self,size): return ImageFont.truetype(self.font_path,size)
    def text(self,value,size=21,gap=10):
        f=self.font(size)
        for paragraph in str(value).split('\n'):
            line=''
            for c in paragraph:
                if self.draw.textlength(line+c,font=f)>CONTENT:
                    self._line(line,f,size);line=''
                line+=c
            self._line(line,f,size)
        self.y+=gap
    def _line(self,line,font,size):
        if self.y+size+8>=self.image.height: raise ValueError('RECEIPT_TOO_LONG')
        self.draw.text((12,self.y),line,font=font,fill=0)
        self.y+=size+8
    def rule(self):
        self.draw.line((12,self.y,371,self.y),fill=0,width=1);self.y+=16
    def photo(self,photo):
        if self.y+photo.height+60>=self.image.height: raise ValueError('RECEIPT_TOO_LONG')
        self.image.paste(photo,((WIDTH-photo.width)//2,self.y));self.y+=photo.height+18
    def qr(self):
        # Integer-sized modules and a four-module quiet zone; never dither QR.
        with Image.open(QR_PATH) as qr:
            self.y+=10
            if self.y+qr.height+48>=self.image.height: raise ValueError('RECEIPT_TOO_LONG')
            self.image.paste(qr,((WIDTH-qr.width)//2,self.y));self.y+=qr.height
        font=self.font(16)
        for line in ('AIMI 체험하기 · 테스트 운영 중', '접속 코드 발급 후 이용하세요'):
            self.draw.text(((WIDTH-self.draw.textlength(line,font=font))/2,self.y),line,font=font,fill=0)
            self.y+=24
        self.y+=2
    def finish(self):
        return self.image.crop((0,0,WIDTH,self.y+48)).point(lambda v:255 if v>=128 else 0,'1')

def number(value):
    return f'{value:,}' if isinstance(value,int) and not isinstance(value,bool) and value>=0 else '미확인'

def tokens(receipt,operation=None):
    calls=[c for c in receipt.get('calls',[]) if operation is None or c['operation']==operation]
    known=[c['total_tokens'] for c in calls if isinstance(c.get('total_tokens'),int)]
    if not known:return '미제공'
    return number(sum(known))+(' (일부 미확인)' if len(known)<len(calls) else '')

def receipt_time(value):
    # Python 3.9 accepts only 3 or 6 fractional digits; Postgres omits trailing
    # zeroes, so valid timestamps can contain 1, 2, 4 or 5 instead.
    normalized=re.sub(r"\.(\d{1,6})(?=(?:[+-]\d{2}:\d{2})?$)",
        lambda match: '.'+match.group(1).ljust(6,'0'),value.replace('Z','+00:00'))
    created=datetime.fromisoformat(normalized)
    if created.tzinfo is None:raise ValueError('RECEIPT_TIMEZONE_REQUIRED')
    return created

def render_receipt(receipt,image_bytes,font_path=None):
    if len(image_bytes)>8_000_000: raise ValueError('IMAGE_TOO_LARGE')
    with Image.open(BytesIO(image_bytes)) as src:
        if src.width*src.height>20_000_000: raise ValueError('IMAGE_TOO_LARGE')
        height=round(src.height*CONTENT/src.width)
        if not 1<=height<=1800: raise ValueError('IMAGE_TOO_TALL')
        photo=atkinson(src.convert('RGBA').resize((PHOTO_WIDTH,round(src.height*PHOTO_WIDTH/src.width)),Image.Resampling.LANCZOS))
    page=Layout(font_path or resolve_font())
    page.text('AI SLOP STOP CHALLENGE',21)
    page.text('AI·발전 체험 영수증',28)
    campaign=receipt.get('campaign') or {}
    created=receipt_time(receipt['created_at'])
    page.text(created.astimezone(ZoneInfo('Asia/Seoul')).strftime('%Y.%m.%d  %H:%M:%S'),19)
    page.rule();page.photo(photo)
    page.text('성공까지 누적 사용량',24)
    page.text('이미지 생성 '+number(receipt.get('generation_calls'))+'회 / 판정 '+number(receipt.get('router_calls'))+'회',20)
    avg=receipt.get('average_prompt_length')
    if isinstance(avg,(int,float)) and avg>=0:
        page.text(f'평균 프롬프트 {avg:,.1f}자\n공백 포함 / 모든 생성 시도',19)
    energy=receipt.get('energy')
    token_only=campaign.get('energy_basis')=='tokens_only' or (not campaign and bool(receipt.get('tokens_by_model')) and all(k.startswith('gemini/') for k in receipt['tokens_by_model']))
    if token_only:
        page.rule();page.text('Gemini 토큰 사용량',24)
        page.text('생성 '+tokens(receipt,'generate'),20)
        page.text('판정 '+tokens(receipt,'router'),20)
        page.text('확인된 합계 '+tokens(receipt),20)
        page.text('토큰만 기록 / Wh 환산 없음',18)
    elif energy:
        page.rule();page.text('누적 GPU 실측 전력량',24)
        page.text('이미지 생성 GPU',18)
        wh=energy.get('energy_wh')
        page.text(f'{wh:.4f} Wh' if isinstance(wh,(int,float)) else '미계측',30)
        router_wh=energy.get('router_energy_wh')
        total_wh=energy.get('total_gpu_energy_wh')
        page.text(f'판정 GPU: {router_wh:.4f} Wh' if isinstance(router_wh,(int,float)) else '판정 GPU: 미계측',18)
        page.text(f'GPU 합계: {total_wh:.4f} Wh' if isinstance(total_wh,(int,float)) else 'GPU 합계: 미계측',22)
    bike=receipt.get('bike')
    if bike:
        page.rule();page.text('자전거 체험 결과',24)
        page.text('환산 발전량',20)
        page.text(f"{bike['campaign_energy_wh']:.4f} Wh",32)
        target=bike.get('target_wh');pct=bike.get('progress_pct')
        if target is not None and pct is not None:
            if token_only:page.text('RTX 4090 GPU 실측 평균 비교 기준\nGemini 소비량 측정/추정값 아님',18)
            page.text(f'목표 {target:.4f} Wh',20)
            page.text(f'환산 달성률 {pct:.1f}%',24)
            page.text('목표 달성' if bike.get('target_reached') else '목표 미달 · 계측 종료',20)
        else:page.text('Gemini · 목표 Wh/달성률 없음',18)
        page.text(f"측정 {bike['elapsed_s']:.1f}초",18)
    page.rule();page.text('성공 프롬프트',24)
    page.text(receipt['prompt'],21)
    if receipt['pass_source']=='router' and receipt.get('router_reason'):
        page.text('통과 이유',24);page.text(receipt['router_reason'],20)
    page.qr()
    return page.finish()

def escpos_bytes(image):
    """ESC * 24-dot bands. No cut command: PT-210 has a manual tear bar."""
    if image.width!=WIDTH or image.mode!='1':raise ValueError('EXPECTED_384_DOT_1BIT_IMAGE')
    data=image.tobytes();stride=WIDTH//8
    out=bytearray(b'\x1b@\x1ba\x01\x1b3\x18')
    for top in range(0,image.height,24):
        out.extend(b'\x1b*\x21\x80\x01')
        for x in range(WIDTH):
            for segment in range(3):
                value=0
                for bit in range(8):
                    y=top+segment*8+bit
                    if y<image.height and not(data[y*stride+x//8]&(0x80>>(x%8))):value|=0x80>>bit
                out.append(value)
        out.append(10)
    out.extend(b'\x1b2\n\n\n')
    return bytes(out)
