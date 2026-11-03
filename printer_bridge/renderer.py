"""384-dot PT-210 receipt renderer. Photos use Atkinson; text stays crisp."""
from __future__ import annotations
from io import BytesIO
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import os
from PIL import Image, ImageDraw, ImageFont

WIDTH = 384
CONTENT = 360

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
        self.image.paste(photo,(12,self.y));self.y+=photo.height+18
    def finish(self):
        return self.image.crop((0,0,WIDTH,self.y+48)).point(lambda v:255 if v>=128 else 0,'1')

def number(value):
    return f'{value:,}' if isinstance(value,int) and not isinstance(value,bool) and value>=0 else '미확인'

def token_number(value, calls, missing):
    return '미계측' if calls > 0 and missing >= calls else number(value)

def render_receipt(receipt,image_bytes,font_path=None):
    if len(image_bytes)>8_000_000: raise ValueError('IMAGE_TOO_LARGE')
    with Image.open(BytesIO(image_bytes)) as src:
        if src.width*src.height>20_000_000: raise ValueError('IMAGE_TOO_LARGE')
        height=round(src.height*CONTENT/src.width)
        if not 1<=height<=1800: raise ValueError('IMAGE_TOO_TALL')
        photo=atkinson(src.convert('RGBA').resize((CONTENT,height),Image.Resampling.LANCZOS))
    page=Layout(font_path or resolve_font())
    page.text('AI SLOP STOP CHALLENGE',21)
    page.text('PASS 영수증',34)
    created=datetime.fromisoformat(receipt['created_at'].replace('Z','+00:00'))
    if created.tzinfo is None: raise ValueError('RECEIPT_TIMEZONE_REQUIRED')
    page.text(created.astimezone(ZoneInfo('Asia/Seoul')).strftime('%Y.%m.%d  %H:%M:%S'),19)
    page.text('승인: '+('STAFF' if receipt['pass_source']=='staff' else 'AI 판정'),19)
    page.rule();page.photo(photo)
    page.text('성공까지 누적 사용량',24)
    page.text('총 사용 토큰' if receipt.get('usage_complete') else '확인된 사용 토큰',20,3)
    page.text(token_number(receipt.get('known_tokens_total'),receipt.get('model_calls_total',0),receipt.get('calls_without_total_tokens',0)),38)
    page.text('이미지 생성 '+number(receipt.get('generation_calls'))+'회 / 판정 '+number(receipt.get('router_calls'))+'회',20)
    avg=receipt.get('average_prompt_length')
    if isinstance(avg,(int,float)) and avg>=0:
        page.text(f'평균 프롬프트 {avg:,.1f}자\n공백 포함 / 모든 생성 시도',19)
    energy=receipt.get('energy')
    if energy:
        page.rule();page.text('이미지 생성 GPU 전력량',24)
        wh=energy.get('energy_wh')
        page.text(f'{wh:.4f} Wh' if isinstance(wh,(int,float)) else '미계측',30)
        page.text(f"계측 {energy.get('measured_generation_calls',0)}회 / 미계측 {energy.get('unmeasured_generation_calls',0)}회",18)
        router_wh=energy.get('router_energy_wh')
        total_wh=energy.get('total_gpu_energy_wh')
        page.text(f'판정 GPU: {router_wh:.4f} Wh' if isinstance(router_wh,(int,float)) else '판정 GPU: 미계측',18)
        page.text(f'GPU 합계: {total_wh:.4f} Wh' if isinstance(total_wh,(int,float)) else 'GPU 합계: 미계측',22)
        page.text('상주 모델 유휴 소비 포함\nCLIP·CPU·냉각·네트워크 제외\n미계측은 0이 아님',18)
    page.rule();page.text('모델별 사용 토큰',24)
    models=receipt.get('tokens_by_model',{})
    for name,usage in sorted(models.items(),key=lambda x:(-bool(x[1].get('generation_calls')),x[0])):
        generation=usage.get('generation_calls',0)>0;router=usage.get('router_calls',0)>0
        label='생성 / 판정' if generation and router else '이미지 생성' if generation else 'AI 판정'
        page.text(label,21,2);page.text(name.split('/',1)[-1],18,3)
        calls,missing=usage.get('calls',0),usage.get('calls_without_total_tokens',0)
        page.text(token_number(usage.get('known_total_tokens'),calls,missing),29)
        if calls > 0 and missing >= calls: page.text('토큰 수 미제공',18)
        elif missing: page.text('일부 토큰 미계측',18)
    if not receipt.get('usage_complete'):page.text('일부 API 사용량 미확인',18)
    page.rule();page.text('성공 프롬프트',24)
    page.text(receipt['prompt'],21)
    if receipt['pass_source']=='router' and receipt.get('router_reason'):
        page.text('통과 이유',24);page.text(receipt['router_reason'],20)
    page.rule();page.text('AI SLOP STOP',20)
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
