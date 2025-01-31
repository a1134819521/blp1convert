from enum import IntEnum
import io
import struct
from PIL import Image

'''
const int MAX__BLP_MIP_MAPS = 16;

struct BLP_HEADER
{
    uint32_t MagicNumber; // BLP0|BLP1|BLP2
    uint32_t Compression; // JPEG：0 | Direct：1
    uint32_t alpha_bits; // 0:1:4:8
    uint32_t Width;
    uint32_t Height;
    uint32_t _extra;
    uint32_t has_mipMaps;
    uint32_t Offset[MAX_NR_OF_BLP_MIP_MAPS];
    uint32_t Size[MAX_NR_OF_BLP_MIP_MAPS];
};
'''

def has_transparency(img: Image):
    if img.mode == "P":
        transparent = img.info.get("transparency", -1)
        for _, index in img.getcolors():
            if index == transparent:
                return True
    elif img.mode == "RGBA":
        extrema = img.getextrema()
        if extrema[3][0] < 255:
            return True
    return False

class Format(IntEnum):
    JPEG = 0
    Direct = 1

class BLP1Decoder():
    def __init__(self, path: str | Image.Image, mipmap_level=0):
        if isinstance(path, Image.Image):
            self.img = path
            return
        self.path = str
        self.fp = io.open(path, "rb")
        self.magic = self.fp.read(4)

        if self.magic != b'BLP1':
            msg = f"不是BLP1文件,是{repr(self.magic)}"
            NotImplementedError(msg)
            return

        (self.content_type,) = struct.unpack("<I", self.fp.read(4))
        (self.alpha_bits,) = struct.unpack("<I", self.fp.read(4))

        (self.width,) = struct.unpack("<I", self.fp.read(4))
        (self.height,) = struct.unpack("<I", self.fp.read(4))

        # flag for alpha channel and team colors (usually 3, 4 or 5)
        # 3 and 4 means color and alpha information for paletted files
        # 5 means only color information, if >=5 on 'unit' textures, it won't show the team color.
        (self.extra,) = struct.unpack("<I", self.fp.read(4))
        (self.has_mipmaps,) = struct.unpack("<I", self.fp.read(4))

        self.mipmap_offsets = struct.unpack("<16I", self.fp.read(16 * 4))
        self.mipmap_sizes = struct.unpack("<16I", self.fp.read(16 * 4))

        # 在未解锁blp大小限制的情况下war3最大支持blp为512*512
        self.max_mipmap_level = 15
        for i in range(16):
            if self.mipmap_sizes[i] == 0:
                self.max_mipmap_level = i - 1
                break

        if mipmap_level > self.max_mipmap_level:
            mipmap_level = self.max_mipmap_level

        self.width = max(int(self.width / (1 << mipmap_level)), 1)
        self.height = max(int(self.height / (1 << mipmap_level)), 1)
        self.size = self.width * self.height

        img_data = {}
        if self.content_type == Format.Direct: # Direct
            palette = []
            # 这个区域大小不是固定的256而是是会变的,根据mipmap_offsets[0]计算一下大小
            palette_size = int(self.mipmap_offsets[0] / 4 - 39)
            for i in range(palette_size):
                b, g, r, a = struct.unpack("<4B", self.fp.read(4))
                # 这里把bgra转换为rgba
                palette.append((r, g, b, a))
            rgb = []
            # 根据mipmap_level算下偏移
            self.fp.read(self.mipmap_offsets[mipmap_level] - self.mipmap_offsets[0])
            for i in range(self.size):
                (data,) = struct.unpack("<B", self.fp.read(1))
                rgb.append(data)
            alpha = []
            if self.alpha_bits != 0:
                for i in range(int((self.size * self.alpha_bits + 7) / 8)):
                    (data,) = struct.unpack("<B", self.fp.read(1))
                    alpha.append(data)
            for i in range(len(rgb)):
                img_data[(i * 4) + 0] = palette[rgb[i]][0] # r
                img_data[(i * 4) + 1] = palette[rgb[i]][1] # g
                img_data[(i * 4) + 2] = palette[rgb[i]][2] # b
                if self.alpha_bits == 0:
                    img_data[(i * 4) + 3] = 0xFF
                elif self.alpha_bits == 1:
                    img_data[(i * 4) + 3] = alpha[i // 8] & (i << (i % 8))
                elif self.alpha_bits == 4:
                    byte = alpha[i // 2]
                    img_data[(i * 4) + 3] = byte >> 4 if i % 2 == 0 else byte & 0xF0
                elif self.alpha_bits == 8:
                    img_data[(i * 4) + 3] = alpha[i]

            emm = bytearray()
            for i in range(self.size):
                d = (img_data[i * 4 + 0], img_data[i * 4 + 1], img_data[i * 4 + 2], img_data[i * 4 + 3])
                emm.extend(d)
            self.img = Image.frombytes('RGBA', (self.width, self.height), emm)

        elif self.content_type == Format.JPEG: # JPEG
            (header_size,) = struct.unpack("<I", self.fp.read(4))
            jpg_header_data = self.fp.read(header_size)
            # 根据mipmap_offsets[0]算偏移->根据mipmap_level可以进行等比缩放
            self.fp.read(self.mipmap_offsets[mipmap_level] - self.fp.tell())
            data = self.fp.read(self.mipmap_sizes[mipmap_level])
            data = jpg_header_data + data
            # 魔兽的颜色处理有点问题这里直接这样转换一下
            image = Image.open(io.BytesIO(data))
            r, g, b, a = image.convert("CMYK").split()
            r = Image.eval(r, lambda a: 255 - a)
            g = Image.eval(g, lambda a: 255 - a)
            b = Image.eval(b, lambda a: 255 - a)
            a = Image.eval(a, lambda a: 255 - a)
            self.img = Image.merge("RGBA", (b, g, r, a))

    def convert(self, out_path: str):
        if self.img is None:
            NotImplementedError("未能解析文件")
            return
        self.img.save(out_path)

    def write(self, out_path: str, format_type = Format.JPEG, mipmap_count: int = -1, compressed = 80):
        if self.img is None:
            NotImplementedError("未能解析文件")
            return
        fp = io.open(out_path, "wb")
        fp.write(b'BLP1')
        fp.write(struct.pack("<i", 0 if format_type == Format.JPEG else 1))
        fp.write(struct.pack("<i", 8 if has_transparency(self.img) else 0))
        fp.write(struct.pack("<i", self.img.width))
        fp.write(struct.pack("<i", self.img.height))
        fp.write(struct.pack("<i", 4))
        fp.write(struct.pack("<i", 1))

        max_size = max(self.img.width, self.img.height)
        mipmap_count = max(min(16, mipmap_count), -1)
        if mipmap_count == -1:
            for i in range(16):
                max_size = int(max_size / 2)
                if max_size == 1:
                    mipmap_count = i + 2
                    break
        
        mipmap_offsets = [0] * 16
        mipmap_sizes = [0] * 16

        current_offset = 28 + 16 * 4 + 16 * 4
        data = bytearray()

        if format_type == Format.Direct:
            alpha = None
            if has_transparency(self.img):
                alpha = self.img.split()[3]
            p = self.img.convert("P")
            palette = p.getpalette("RGB")
            for i in range(256):
                data += struct.pack("<4B", palette[i*3+2], palette[i*3+1], palette[i*3+0], 255)
            for i in range(mipmap_count):
                data1 = bytearray()
                mipmap_img = p.resize((max(1, int(self.img.width / (1 << i))), max(1, int(self.img.height / (1 << i)))))
                a = None
                if alpha:
                    a = alpha.resize((max(1, int(self.img.width / (1 << i))), max(1, int(self.img.height / (1 << i)))))
                    pass

                for pixel in mipmap_img.getdata():
                    data1 += struct.pack("<B", pixel)
                if a:
                    for pixel in a.getdata():
                        data1 += struct.pack("<B", pixel)
                mipmap_offsets[i] = current_offset + len(data)
                mipmap_sizes[i] = len(data1)
                data += data1

        elif format_type == Format.JPEG:
            # 这里好像是可以吧jpeg的头部信息拆出来合并在最前面略微压缩图片大小
            data += struct.pack("<I", 0)
            for i in range(mipmap_count):
                mipmap_img = self.img.resize((max(1, int(self.img.width / (1 << i))), max(1, int(self.img.height / (1 << i)))))
                c, m, y, k = mipmap_img.convert('RGBA').split()
                c = Image.eval(c, lambda a: 255 - a)
                m = Image.eval(m, lambda a: 255 - a)
                y = Image.eval(y, lambda a: 255 - a)
                k = Image.eval(k, lambda a: 255 - a)
                mipmap_img = Image.merge("CMYK", (y, m, c, k))

                data1 = bytearray()
                with io.BytesIO() as output:
                    mipmap_img.save(output, format="JPEG", quality=compressed)
                    jpg_data = output.getvalue()
                    data1 += jpg_data
                mipmap_offsets[i] = current_offset + len(data)
                mipmap_sizes[i] = len(data1)
                data += data1

        fp.write(struct.pack("<16I", *mipmap_offsets))
        fp.write(struct.pack("<16I", *mipmap_sizes))
        fp.write(data)
        fp.close()
