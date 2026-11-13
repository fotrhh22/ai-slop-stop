"""Prepare physical PT-210 tests. Does not submit any print job."""
from pathlib import Path
import importlib.util
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader
from reportlab.graphics.barcode.qr import QrCodeWidget
import subprocess

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'output/pt210-test'
OUT.mkdir(parents=True,exist_ok=True)
SOURCE=Path('/Users/hunhokang/Desktop/머리/AI SLOP STOP CHALLENGE.pdf')
FONT='/System/Library/Fonts/Supplemental/AppleGothic.ttf'
W=384
PAGE=PdfReader(SOURCE).pages[0]
photo=PAGE.images[0].image.convert('RGB')
photo.save(OUT/'source-photo.png')
photo=photo.resize((360,round(photo.height*360/photo.width)),Image.Resampling.LANCZOS).convert('RGBA')
(OUT/'source.rgba').write_bytes(photo.tobytes())
subprocess.run(['/Users/hunhokang/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node',str(ROOT/'printer_bridge/dither_test.cjs')],check=True)
spec=importlib.util.spec_from_file_location('adapter','/Users/hunhokang/Library/Application Support/PT210/bin/pt210-pdf-to-escpos.py')
adapter=importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)

def font(size): return ImageFont.truetype(FONT,size)
class Strip:
 def __init__(self):
  self.im=Image.new('L',(W,16000),255);self.d=ImageDraw.Draw(self.im);self.y=16
 def text(self,text,size=21,gap=10):
  f=font(size)
  for paragraph in text.split('\n'):
   line=''
   for c in paragraph:
    if self.d.textlength(line+c,font=f)>360:
     self.d.text((12,self.y),line,font=f,fill=0);self.y+=size+8;line=''
    line+=c
   self.d.text((12,self.y),line,font=f,fill=0);self.y+=size+8
  self.y+=gap
 def image(self,im):
  self.im.paste(im,(int((W-im.width)/2),self.y));self.y+=im.height+18
 def line(self):
  self.d.line((12,self.y,371,self.y),fill=0,width=1);self.y+=18
 def save(self,stem):
  im=self.im.crop((0,0,W,self.y+48)).point(lambda v:255 if v>=128 else 0,'1')
  im.save(OUT/(stem+'.png'))
  white=im.tobytes(); black=bytes(x^255 for x in white)
  raw=adapter.escpos_escstar(black,im.height)
  (OUT/(stem+'.escpos')).write_bytes(raw)
  return im

s=Strip();s.text('AI SLOP STOP',28);s.text('PT-210 한글 / QR 연결 테스트',22);s.line()
s.text('가나다라마바사 아자차카타파하\n한글과 English 0123456789\n금액 12,078 / 평균 24.5자',21)
s.text('위 숫자는 출력 확인용 예시입니다.',17)
q=QrCodeWidget('https://ai-slop-stop.vercel.app/');q.qr.make();n=q.qr.getModuleCount();module=5
qr=Image.new('L',((n+8)*module,(n+8)*module),255);d=ImageDraw.Draw(qr)
for y in range(n):
 for x in range(n):
  if q.qr.isDark(y,x):d.rectangle(((x+4)*module,(y+4)*module,(x+5)*module-1,(y+5)*module-1),fill=0)
s.image(qr);s.text('QR: 배포된 챌린지 홈페이지',18);s.save('01-korean-qr')

methods=['atkinson','floyd-steinberg','ordered-bayer','ordered-blue-noise']
s=Strip();s.text('DITHER COMPARISON',26);s.text('같은 사진 / 360 dots\n밝기 1.00 / 대비 1.00',20);s.line()
for i,method in enumerate(methods,1):
 im=Image.frombytes('RGBA',photo.size,(OUT/(method+'.rgba')).read_bytes()).convert('L')
 im.save(OUT/(method+'.png'))
 s.text(f'{i:02d}  {method}',20);s.image(im);s.line()
s.text('위에서부터 01 / 02 / 03 / 04',18);s.save('02-dither-comparison')

s=Strip();s.text('AI SLOP STOP CHALLENGE',21);s.text('PASS 영수증',32)
s.text('보관 영수증 재출력 테스트',18);s.line()
s.text('성공 시각\n2026. 10. 19. 오후 2:56:29\n승인 방식: AI 판정',20)
s.image(Image.open(OUT/'atkinson.png'))
s.text('성공 프롬프트',25)
s.text('폭격기, 머리 부분이 악어의 머리로 대체됨, not realistic animation style, 색상은 은색, 배경은 맑은 구름, 폭격기 기준 아래쪽에서 바라보는 구도, 상체 부분만 보이도록 처리, 폭탄의 위치는 폭격기 바로 아래에 매달려있음',21)
s.text('통과 이유',25)
s.text('생성된 이미지는 악어 머리가 달린 비행기, 폭탄, 구름 배경 등의 주요 요소를 매우 정확하게 재현했습니다.',21)
s.line();s.text('성공까지 누적 사용량',25)
s.text('이미지 생성 요청: 3회\n판정 요청: 3회\n총 사용 토큰: 12,078',21)
s.text('모델별 사용 토큰',25)
s.text('이미지 생성\ngemini-3.1-flash-lite-image\n4,575\n\nAI 판정\ngemini-3.5-flash-lite\n7,503',21)
s.line();s.text('PT-210 / 384 dots / Atkinson',18);s.save('03-archived-receipt')
print('Prepared:',[(p.name,Image.open(p).size) for p in OUT.glob('0*.png')])
