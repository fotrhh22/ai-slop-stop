#import <Foundation/Foundation.h>
#import <IOBluetooth/IOBluetooth.h>

static void emit(NSDictionary *value) {
    NSData *data=[NSJSONSerialization dataWithJSONObject:value options:NSJSONWritingPrettyPrinted error:nil];
    fwrite(data.bytes,1,data.length,stdout); fputc('\n',stdout); fflush(stdout);
}
static BOOL printerName(NSString *name) {
    NSString *lower=name.lowercaseString ?: @"";
    for(NSString *hint in @[@"pt-210",@"pt210",@"mtp",@"printer",@"goojprt"])
        if([lower containsString:hint]) return YES;
    return NO;
}
static void pump(double seconds) {
    [[NSRunLoop currentRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:seconds]];
}
@interface Probe : NSObject <IOBluetoothDeviceInquiryDelegate>
@property BOOL inquiryDone;
@property BOOL sdpDone;
@property IOReturn sdpStatus;
@property IOReturn inquiryStatus;
@end
@implementation Probe
- (void)deviceInquiryComplete:(IOBluetoothDeviceInquiry *)sender error:(IOReturn)error aborted:(BOOL)aborted {
    self.inquiryStatus=error; self.inquiryDone=YES;
}
- (void)sdpQueryComplete:(IOBluetoothDevice *)device status:(IOReturn)status {
    self.sdpStatus=status; self.sdpDone=YES;
}
@end
static NSDictionary *deviceInfo(IOBluetoothDevice *device) {
    return @{@"name":device.name ?: @"", @"address":device.addressString ?: @"", @"paired":@(device.isPaired)};
}
int main(int argc,const char *argv[]) {
 @autoreleasepool {
    NSMutableDictionary *args=[NSMutableDictionary dictionary];
    for(int i=1;i<argc;i++) {
        NSString *key=@(argv[i]);
        if([@[@"--dry-run",@"--scan",@"--inspect",@"--send"] containsObject:key]) args[key]=@YES;
        else if(i+1<argc) args[key]=@(argv[++i]);
        else {emit(@{@"error":@"MISSING_ARGUMENT"});return 2;}
    }
    if(args[@"--dry-run"]) {
        NSData *data=[NSData dataWithContentsOfFile:args[@"--file"] ?: @""];
        if(!data.length) {emit(@{@"error":@"EMPTY_OR_MISSING_FILE"});return 2;}
        emit(@{@"mode":@"native_spp_dry_run",@"bytes":@(data.length),@"radio_access":@NO,@"writes":@0});return 0;
    }
    Probe *probe=[Probe new];
    if(args[@"--scan"]) {
        IOBluetoothDeviceInquiry *inquiry=[IOBluetoothDeviceInquiry inquiryWithDelegate:probe];
        inquiry.inquiryLength=10; inquiry.updateNewDeviceNames=YES;
        IOReturn status=[inquiry start];
        if(status!=kIOReturnSuccess) {emit(@{@"error":@"INQUIRY_START_FAILED",@"status":@(status)});return 3;}
        double deadline=NSProcessInfo.processInfo.systemUptime+25;
        while(!probe.inquiryDone && NSProcessInfo.processInfo.systemUptime<deadline) pump(.05);
        [inquiry stop];
        NSMutableArray *devices=[NSMutableArray array];
        for(IOBluetoothDevice *device in inquiry.foundDevices)
            if(printerName(device.name)) [devices addObject:deviceInfo(device)];
        emit(@{@"mode":@"classic_inquiry",@"completed":@(probe.inquiryDone),@"status":@(probe.inquiryStatus),@"devices":devices,
               @"note":@"Names are hints; inspect the selected device for SPP 0x1101."});return probe.inquiryDone?0:3;
    }
    NSString *address=args[@"--address"];
    NSRegularExpression *pattern=[NSRegularExpression regularExpressionWithPattern:@"^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$" options:0 error:nil];
    if(!address || [pattern numberOfMatchesInString:address options:0 range:NSMakeRange(0,address.length)]!=1) {
        emit(@{@"error":@"VALID_CLASSIC_MAC_ADDRESS_REQUIRED"});return 2;
    }
    IOBluetoothDevice *device=[IOBluetoothDevice deviceWithAddressString:address];
    if(!device) {emit(@{@"error":@"DEVICE_NOT_FOUND"});return 3;}
    NSString *channelText=args[@"--channel"] ?: @"auto";
    BluetoothRFCOMMChannelID channel=0;
    if([channelText isEqualToString:@"auto"] || args[@"--inspect"]) {
        IOReturn status=[device performSDPQuery:probe];
        if(status!=kIOReturnSuccess) {emit(@{@"error":@"SDP_START_FAILED",@"status":@(status)});return 3;}
        double deadline=NSProcessInfo.processInfo.systemUptime+15;
        while(!probe.sdpDone && NSProcessInfo.processInfo.systemUptime<deadline) pump(.05);
        if(!probe.sdpDone || probe.sdpStatus!=kIOReturnSuccess) {
            emit(@{@"error":@"SDP_QUERY_FAILED_OR_TIMED_OUT",@"status":@(probe.sdpStatus)});return 3;
        }
        IOBluetoothSDPServiceRecord *record=[device getServiceRecordForUUID:[IOBluetoothSDPUUID uuid16:0x1101]];
        if(!record || [record getRFCOMMChannelID:&channel]!=kIOReturnSuccess) {
            emit(@{@"error":@"SPP_SERVICE_0x1101_NOT_FOUND",@"device":deviceInfo(device)});return 3;
        }
    } else {
        NSInteger number=channelText.integerValue;
        if(number<1 || number>30) {emit(@{@"error":@"CHANNEL_MUST_BE_1_TO_30"});return 2;}
        channel=(BluetoothRFCOMMChannelID)number;
    }
    if(args[@"--inspect"]) {
        emit(@{@"mode":@"classic_sdp",@"device":deviceInfo(device),@"spp_service":@"0x1101",@"rfcomm_channel":@(channel),@"writes":@0});return 0;
    }
    if(!args[@"--send"]) {emit(@{@"error":@"SEND_FLAG_REQUIRED"});return 2;}
    if(!device.isPaired) {emit(@{@"error":@"PAIR_IN_MACOS_SETTINGS_BEFORE_SEND"});return 3;}
    NSData *data=[NSData dataWithContentsOfFile:args[@"--file"] ?: @""];
    NSInteger chunk=args[@"--chunk"]?[args[@"--chunk"] integerValue]:182;
    double delay=args[@"--delay-ms"]?[args[@"--delay-ms"] doubleValue]:40;
    if(!data.length || data.length>2000000 || chunk<1 || chunk>512 || !isfinite(delay) || delay<0 || delay>1000) {
        emit(@{@"error":@"INVALID_FILE_OR_TRANSMISSION_PARAMETERS"});return 2;
    }
    IOBluetoothRFCOMMChannel *link=nil;
    IOReturn status=[device openRFCOMMChannelSync:&link withChannelID:channel delegate:nil];
    if(status!=kIOReturnSuccess || !link.isOpen) {
        emit(@{@"error":@"RFCOMM_OPEN_FAILED",@"status":@(status),@"bytes_completed":@0});return 3;
    }
    NSUInteger mtu=link.getMTU;
    if(mtu==0) {[link closeChannel];emit(@{@"error":@"INVALID_RFCOMM_MTU"});return 3;}
    chunk=MIN(chunk,mtu);
    NSUInteger completed=0,attempted=0;
    double start=NSProcessInfo.processInfo.systemUptime;
    while(completed<data.length) {
        double deadline=NSProcessInfo.processInfo.systemUptime+10;
        while(link.isOpen && link.isTransmissionPaused && NSProcessInfo.processInfo.systemUptime<deadline) pump(.01);
        if(!link.isOpen || link.isTransmissionPaused) {
            [link closeChannel];emit(@{@"state":@"UNKNOWN",@"error":@"LINK_CLOSED_OR_FLOW_CONTROL_TIMEOUT",@"bytes_completed":@(completed),@"attempted_bytes":@(attempted)});return 4;
        }
        NSUInteger count=MIN((NSUInteger)chunk,data.length-completed);
        attempted=completed+count;
        status=[link writeSync:(void *)((const uint8_t *)data.bytes+completed) length:(UInt16)count];
        if(status!=kIOReturnSuccess) {
            [link closeChannel];emit(@{@"state":@"UNKNOWN",@"error":@"RFCOMM_WRITE_FAILED",@"status":@(status),@"bytes_completed":@(completed),@"attempted_bytes":@(attempted)});return 4;
        }
        completed+=count;
        if(completed<data.length) pump(delay/1000);
    }
    [link closeChannel];
    emit(@{@"state":@"TRANSPORT_WRITES_COMPLETED",@"bytes_completed":@(completed),@"rfcomm_channel":@(channel),
           @"send_seconds":@(NSProcessInfo.processInfo.systemUptime-start),@"paper_verified":@NO});return 0;
 }
}
