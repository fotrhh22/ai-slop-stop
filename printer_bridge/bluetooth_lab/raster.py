"""Pre-encode the existing 1-bit receipt as GS v 0 strips, preserving its pixels."""
from __future__ import annotations
import argparse
from pathlib import Path
import struct


def encode_gsv0(image,strip_rows=128):
    if image.mode!='1' or image.width!=384:raise ValueError('Use the original 384-dot 1-bit preview')
    if not 1<=strip_rows<=512:raise ValueError('strip_rows must be 1..512')
    white=image.tobytes();stride=48
    result=bytearray(b'\x1b@\x1ba\x00')
    for top in range(0,image.height,strip_rows):
        rows=min(strip_rows,image.height-top)
        result.extend(b'\x1dv0\x00'+struct.pack('<HH',stride,rows))
        result.extend(bytes(value^0xff for value in white[top*stride:(top+rows)*stride]))
    result.extend(b'\n\n\n')
    return bytes(result)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--strip-rows',type=int,default=128)
    parser.add_argument('--top-rows',type=int,help='Only prepare a short top-of-receipt smoke test')
    args=parser.parse_args()
    from PIL import Image
    with Image.open(args.image) as image:
        if args.top_rows:
            if not 1<=args.top_rows<=image.height:parser.error('Invalid top-rows')
            image=image.crop((0,0,image.width,args.top_rows))
        payload=encode_gsv0(image,args.strip_rows)
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_bytes(payload)
    print(f'Prepared {len(payload)} bytes: {args.output} (no printer access)')


if __name__=='__main__':main()
